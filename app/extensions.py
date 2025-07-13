"""
Flask extensions and third-party integrations.
Centralized initialization of Sentry, rate limiting, circuit breakers, and logging.
"""

import os
import logging
import sys
from datetime import datetime
import redis
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
from sentry_sdk.integrations.redis import RedisIntegration
from flask_sqlalchemy import SQLAlchemy
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import pybreaker
from pythonjsonlogger import jsonlogger

# Initialize extensions
db = SQLAlchemy()
redis_client = None
limiter = None

# Circuit breakers for external services
openai_breaker = None
anthropic_breaker = None
database_breaker = None

def init_sentry(app):
    """Initialize Sentry error tracking and performance monitoring"""
    sentry_dsn = os.getenv('SENTRY_DSN')
    environment = os.getenv('FLASK_ENV', 'development')
    
    if sentry_dsn and environment == 'production':
        sentry_sdk.init(
            dsn=sentry_dsn,
            integrations=[
                FlaskIntegration(transaction_style='endpoint'),
                SqlalchemyIntegration(),
                RedisIntegration(),
            ],
            traces_sample_rate=0.1,  # 10% of transactions for performance monitoring
            profiles_sample_rate=0.1,  # 10% for profiling
            environment=environment,
            release=os.getenv('RAILWAY_GIT_COMMIT_SHA', 'unknown'),
            before_send=filter_sensitive_data,
            attach_stacktrace=True,
            send_default_pii=False,
        )
        app.logger.info("Sentry initialized for production environment")
    else:
        app.logger.info("Sentry not initialized - missing DSN or not in production")

def filter_sensitive_data(event, hint):
    """Filter sensitive data from Sentry events"""
    # Remove sensitive headers
    if 'request' in event and 'headers' in event['request']:
        headers = event['request']['headers']
        sensitive_headers = ['authorization', 'x-api-key', 'cookie', 'x-auth-token']
        for header in sensitive_headers:
            if header in headers:
                headers[header] = '[Filtered]'
    
    # Remove sensitive form data
    if 'request' in event and 'data' in event['request']:
        data = event['request']['data']
        if isinstance(data, dict):
            sensitive_fields = ['password', 'token', 'secret', 'key']
            for field in sensitive_fields:
                if field in data:
                    data[field] = '[Filtered]'
    
    return event

def init_redis(app):
    """Initialize Redis client with connection pooling"""
    global redis_client
    
    redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
    
    try:
        redis_client = redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
            health_check_interval=30,
            max_connections=20
        )
        
        # Test connection
        redis_client.ping()
        app.logger.info("Redis client initialized successfully")
        
    except redis.RedisError as e:
        app.logger.error(f"Failed to initialize Redis: {e}")
        # Use a mock Redis client for development
        redis_client = MockRedisClient()

class MockRedisClient:
    """Mock Redis client for development/testing when Redis is unavailable"""
    
    def __init__(self):
        self._data = {}
    
    def get(self, key):
        return self._data.get(key)
    
    def set(self, key, value, ex=None):
        self._data[key] = value
        return True
    
    def delete(self, key):
        return self._data.pop(key, None) is not None
    
    def ping(self):
        return True
    
    def incr(self, key, amount=1):
        current = int(self._data.get(key, 0))
        self._data[key] = str(current + amount)
        return current + amount
    
    def expire(self, key, seconds):
        return True

def init_rate_limiter(app):
    """Initialize Flask-Limiter with Redis backend"""
    global limiter
    
    # Custom key function that can use API keys or IP addresses
    def get_rate_limit_key():
        from flask import request, g
        
        # Try to get API key from headers
        api_key = request.headers.get('X-API-Key')
        if api_key:
            return f"api_key:{api_key}"
        
        # Try to get user ID from session/auth
        if hasattr(g, 'current_user') and g.current_user:
            return f"user:{g.current_user.id}"
        
        # Fall back to IP address
        return get_remote_address()
    
    storage_uri = os.getenv('REDIS_URL', 'redis://localhost:6379/1')
    
    limiter = Limiter(
        app=app,
        key_func=get_rate_limit_key,
        storage_uri=storage_uri,
        storage_options={
            'socket_connect_timeout': 30,
            'socket_timeout': 30,
            'retry_on_timeout': True
        },
        strategy="fixed-window",
        default_limits=["1000 per hour", "100 per minute"],
        headers_enabled=True,
        swallow_errors=True  # Don't break app if Redis is down
    )
    
    app.logger.info("Rate limiter initialized with Redis backend")

def init_circuit_breakers(app):
    """Initialize circuit breakers for external services"""
    global openai_breaker, anthropic_breaker, database_breaker
    
    # Circuit breaker for OpenAI API
    openai_breaker = pybreaker.CircuitBreaker(
        fail_max=5,
        reset_timeout=60,
        name="openai_api",
        listeners=[CircuitBreakerLogger(app.logger)]
    )
    
    # Circuit breaker for Anthropic API
    anthropic_breaker = pybreaker.CircuitBreaker(
        fail_max=5,
        reset_timeout=60,
        name="anthropic_api",
        listeners=[CircuitBreakerLogger(app.logger)]
    )
    
    # Circuit breaker for database operations
    database_breaker = pybreaker.CircuitBreaker(
        fail_max=3,
        reset_timeout=30,
        name="database",
        listeners=[CircuitBreakerLogger(app.logger)]
    )
    
    app.logger.info("Circuit breakers initialized for external services")

class CircuitBreakerLogger(pybreaker.CircuitBreakerListener):
    """Custom circuit breaker listener for logging state changes"""
    
    def __init__(self, logger):
        self.logger = logger
    
    def state_change(self, cb, old_state, new_state):
        self.logger.warning(
            f"Circuit breaker '{cb.name}' changed state from {old_state} to {new_state}"
        )
    
    def failure(self, cb, exc):
        self.logger.error(
            f"Circuit breaker '{cb.name}' recorded failure: {exc}"
        )
    
    def success(self, cb):
        self.logger.debug(
            f"Circuit breaker '{cb.name}' recorded success"
        )

def init_structured_logging(app):
    """Initialize structured JSON logging for production"""
    
    if os.getenv('FLASK_ENV') == 'production':
        # Configure JSON logging for production
        json_handler = logging.StreamHandler(sys.stdout)
        json_formatter = jsonlogger.JsonFormatter(
            fmt='%(asctime)s %(name)s %(levelname)s %(message)s',
            datefmt='%Y-%m-%dT%H:%M:%S'
        )
        json_handler.setFormatter(json_formatter)
        
        # Set up root logger
        root_logger = logging.getLogger()
        root_logger.handlers.clear()
        root_logger.addHandler(json_handler)
        root_logger.setLevel(logging.INFO)
        
        # Configure Flask app logger
        app.logger.handlers.clear()
        app.logger.addHandler(json_handler)
        app.logger.setLevel(logging.INFO)
        app.logger.propagate = False
        
        # Configure SQLAlchemy logging
        sqlalchemy_logger = logging.getLogger('sqlalchemy.engine')
        sqlalchemy_logger.setLevel(logging.WARNING)
        
        app.logger.info("Structured JSON logging initialized")
    else:
        # Use default logging for development
        logging.basicConfig(
            level=logging.DEBUG,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        app.logger.info("Development logging initialized")

def init_extensions(app):
    """Initialize all Flask extensions"""
    
    # Initialize structured logging first
    init_structured_logging(app)
    
    # Initialize Sentry for error tracking
    init_sentry(app)
    
    # Initialize database
    db.init_app(app)
    
    # Initialize Redis
    init_redis(app)
    
    # Initialize rate limiter
    init_rate_limiter(app)
    
    # Initialize circuit breakers
    init_circuit_breakers(app)
    
    app.logger.info("All extensions initialized successfully")

# Utility functions for accessing circuit breakers
def get_openai_breaker():
    """Get OpenAI circuit breaker instance"""
    return openai_breaker

def get_anthropic_breaker():
    """Get Anthropic circuit breaker instance"""
    return anthropic_breaker

def get_database_breaker():
    """Get database circuit breaker instance"""
    return database_breaker