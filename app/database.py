"""
Enhanced database configuration with connection pooling, caching, and monitoring.
Production-optimized SQLAlchemy setup with Redis caching layer.
"""

import os
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Any, Dict
import json
import hashlib
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import QueuePool
from flask_sqlalchemy import SQLAlchemy
from flask import current_app
import redis

logger = logging.getLogger(__name__)

class DatabaseConfig:
    """Production database configuration with optimized settings"""
    
    @staticmethod
    def get_database_url():
        """Get database URL with fallback options"""
        # Try Railway database URL first
        database_url = os.getenv('DATABASE_URL')
        if database_url:
            # Fix postgres:// to postgresql:// for SQLAlchemy 1.4+
            if database_url.startswith('postgres://'):
                database_url = database_url.replace('postgres://', 'postgresql://', 1)
            return database_url
        
        # Fallback to individual components
        db_host = os.getenv('DB_HOST', 'localhost')
        db_port = os.getenv('DB_PORT', '5432')
        db_name = os.getenv('DB_NAME', 'biped')
        db_user = os.getenv('DB_USER', 'postgres')
        db_password = os.getenv('DB_PASSWORD', '')
        
        return f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
    
    @staticmethod
    def get_engine_config():
        """Get optimized engine configuration for production"""
        is_production = os.getenv('FLASK_ENV') == 'production'
        
        config = {
            'pool_size': int(os.getenv('DB_POOL_SIZE', '10')),
            'max_overflow': int(os.getenv('DB_MAX_OVERFLOW', '20')),
            'pool_timeout': int(os.getenv('DB_POOL_TIMEOUT', '30')),
            'pool_recycle': int(os.getenv('DB_POOL_RECYCLE', '3600')),  # 1 hour
            'pool_pre_ping': True,  # Enable connection health checks
            'poolclass': QueuePool,
            'echo': not is_production,  # Disable SQL logging in production
            'echo_pool': os.getenv('DB_ECHO_POOL', 'false').lower() == 'true',
            'future': True,  # Use SQLAlchemy 2.0 style
        }
        
        # Production-specific optimizations
        if is_production:
            config.update({
                'pool_reset_on_return': 'commit',  # Reset connections on return
                'connect_args': {
                    'connect_timeout': 10,
                    'application_name': 'biped_app',
                    'options': '-c default_transaction_isolation=read_committed'
                }
            })
        
        return config

class CacheManager:
    """Redis-based caching manager for database queries and application data"""
    
    def __init__(self, redis_client):
        self.redis_client = redis_client
        self.default_ttl = int(os.getenv('CACHE_DEFAULT_TTL', '3600'))  # 1 hour
        self.key_prefix = os.getenv('CACHE_KEY_PREFIX', 'biped:')
    
    def _make_key(self, key: str) -> str:
        """Generate cache key with prefix"""
        return f"{self.key_prefix}{key}"
    
    def get(self, key: str) -> Optional[Any]:
        """Get value from cache"""
        try:
            cache_key = self._make_key(key)
            value = self.redis_client.get(cache_key)
            if value:
                return json.loads(value)
            return None
        except (redis.RedisError, json.JSONDecodeError) as e:
            logger.warning(f"Cache get error for key {key}: {e}")
            return None
    
    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """Set value in cache"""
        try:
            cache_key = self._make_key(key)
            ttl = ttl or self.default_ttl
            serialized_value = json.dumps(value, default=str)
            return self.redis_client.set(cache_key, serialized_value, ex=ttl)
        except (redis.RedisError, TypeError) as e:
            logger.warning(f"Cache set error for key {key}: {e}")
            return False
    
    def delete(self, key: str) -> bool:
        """Delete value from cache"""
        try:
            cache_key = self._make_key(key)
            return bool(self.redis_client.delete(cache_key))
        except redis.RedisError as e:
            logger.warning(f"Cache delete error for key {key}: {e}")
            return False
    
    def invalidate_pattern(self, pattern: str) -> int:
        """Invalidate all keys matching pattern"""
        try:
            cache_pattern = self._make_key(pattern)
            keys = self.redis_client.keys(cache_pattern)
            if keys:
                return self.redis_client.delete(*keys)
            return 0
        except redis.RedisError as e:
            logger.warning(f"Cache invalidate pattern error for {pattern}: {e}")
            return 0
    
    def cache_query_result(self, query_hash: str, result: Any, ttl: Optional[int] = None):
        """Cache database query result"""
        cache_key = f"query:{query_hash}"
        return self.set(cache_key, result, ttl)
    
    def get_cached_query_result(self, query_hash: str) -> Optional[Any]:
        """Get cached database query result"""
        cache_key = f"query:{query_hash}"
        return self.get(cache_key)
    
    def generate_query_hash(self, query: str, params: Dict = None) -> str:
        """Generate hash for database query caching"""
        query_string = f"{query}:{json.dumps(params or {}, sort_keys=True)}"
        return hashlib.md5(query_string.encode()).hexdigest()

class DatabaseMonitor:
    """Database performance monitoring and health tracking"""
    
    def __init__(self):
        self.query_stats = {}
        self.slow_query_threshold = float(os.getenv('SLOW_QUERY_THRESHOLD', '1.0'))
    
    def record_query(self, query: str, duration: float, success: bool = True):
        """Record query execution statistics"""
        query_type = self._get_query_type(query)
        
        if query_type not in self.query_stats:
            self.query_stats[query_type] = {
                'count': 0,
                'total_duration': 0,
                'avg_duration': 0,
                'slow_queries': 0,
                'errors': 0
            }
        
        stats = self.query_stats[query_type]
        stats['count'] += 1
        
        if success:
            stats['total_duration'] += duration
            stats['avg_duration'] = stats['total_duration'] / stats['count']
            
            if duration > self.slow_query_threshold:
                stats['slow_queries'] += 1
                logger.warning(
                    f"Slow query detected: {query_type} took {duration:.3f}s"
                )
        else:
            stats['errors'] += 1
    
    def _get_query_type(self, query: str) -> str:
        """Extract query type from SQL statement"""
        query = query.strip().upper()
        if query.startswith('SELECT'):
            return 'SELECT'
        elif query.startswith('INSERT'):
            return 'INSERT'
        elif query.startswith('UPDATE'):
            return 'UPDATE'
        elif query.startswith('DELETE'):
            return 'DELETE'
        else:
            return 'OTHER'
    
    def get_stats(self) -> Dict:
        """Get current database statistics"""
        return {
            'query_stats': self.query_stats,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }

# Global instances
cache_manager = None
db_monitor = DatabaseMonitor()

def init_database(app):
    """Initialize database with production optimizations"""
    global cache_manager
    
    # Get database configuration
    database_url = DatabaseConfig.get_database_url()
    engine_config = DatabaseConfig.get_engine_config()
    
    # Configure SQLAlchemy
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = engine_config
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    
    # Initialize cache manager
    from app.extensions import redis_client
    cache_manager = CacheManager(redis_client)
    
    logger.info("Database configuration initialized")

def setup_database_events(db: SQLAlchemy):
    """Setup database event listeners for monitoring and optimization"""
    
    @event.listens_for(Engine, "before_cursor_execute")
    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        """Record query start time"""
        context._query_start_time = time.time()
    
    @event.listens_for(Engine, "after_cursor_execute")
    def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        """Record query completion and statistics"""
        if hasattr(context, '_query_start_time'):
            duration = time.time() - context._query_start_time
            db_monitor.record_query(statement, duration, success=True)
    
    @event.listens_for(Engine, "handle_error")
    def handle_error(exception_context):
        """Record query errors"""
        statement = getattr(exception_context, 'statement', 'UNKNOWN')
        db_monitor.record_query(statement, 0, success=False)
        logger.error(f"Database error: {exception_context.original_exception}")
    
    @event.listens_for(Engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        """Set database-specific optimizations on connection"""
        # This example is for PostgreSQL, adjust for your database
        if 'postgresql' in str(dbapi_connection):
            with dbapi_connection.cursor() as cursor:
                # Set connection-level optimizations
                cursor.execute("SET statement_timeout = '30s'")
                cursor.execute("SET lock_timeout = '10s'")
                cursor.execute("SET idle_in_transaction_session_timeout = '60s'")
    
    logger.info("Database event listeners configured")

class QueryCache:
    """Decorator for caching database query results"""
    
    def __init__(self, ttl: int = 3600, key_prefix: str = ""):
        self.ttl = ttl
        self.key_prefix = key_prefix
    
    def __call__(self, func):
        def wrapper(*args, **kwargs):
            if not cache_manager:
                return func(*args, **kwargs)
            
            # Generate cache key from function name and arguments
            cache_key = f"{self.key_prefix}:{func.__name__}:{hash(str(args) + str(kwargs))}"
            
            # Try to get from cache
            cached_result = cache_manager.get(cache_key)
            if cached_result is not None:
                return cached_result
            
            # Execute function and cache result
            result = func(*args, **kwargs)
            cache_manager.set(cache_key, result, self.ttl)
            
            return result
        
        return wrapper

def get_cache_manager() -> Optional[CacheManager]:
    """Get global cache manager instance"""
    return cache_manager

def get_db_monitor() -> DatabaseMonitor:
    """Get global database monitor instance"""
    return db_monitor

def health_check_database() -> Dict:
    """Perform database health check"""
    try:
        from app.extensions import db
        
        start_time = time.time()
        
        # Test basic connectivity
        db.session.execute(text('SELECT 1'))
        
        # Test connection pool status
        pool = db.engine.pool
        pool_status = {
            'size': pool.size(),
            'checked_out': pool.checkedout(),
            'overflow': pool.overflow(),
            'checked_in': pool.checkedin()
        }
        
        duration = time.time() - start_time
        
        return {
            'healthy': True,
            'response_time_ms': round(duration * 1000, 2),
            'pool_status': pool_status,
            'query_stats': db_monitor.get_stats(),
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        
    except Exception as e:
        return {
            'healthy': False,
            'error': str(e),
            'timestamp': datetime.now(timezone.utc).isoformat()
        }