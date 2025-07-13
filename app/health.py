"""
Health check endpoints for Flask application monitoring and observability.
Implements comprehensive health checks for database, Redis, and external services.
"""

from flask import Blueprint, jsonify, current_app
import time
import psutil
import redis
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
import requests
from datetime import datetime, timezone
import os

health_bp = Blueprint('health', __name__)

class HealthChecker:
    """Centralized health checking functionality"""
    
    def __init__(self):
        self.checks = {}
        self.start_time = time.time()
    
    def register_check(self, name, check_func):
        """Register a health check function"""
        self.checks[name] = check_func
    
    def run_all_checks(self):
        """Run all registered health checks"""
        results = {
            'status': 'healthy',
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'uptime': time.time() - self.start_time,
            'checks': {}
        }
        
        overall_healthy = True
        
        for name, check_func in self.checks.items():
            try:
                check_result = check_func()
                results['checks'][name] = check_result
                if not check_result.get('healthy', False):
                    overall_healthy = False
            except Exception as e:
                results['checks'][name] = {
                    'healthy': False,
                    'error': str(e),
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }
                overall_healthy = False
        
        results['status'] = 'healthy' if overall_healthy else 'unhealthy'
        return results

# Global health checker instance
health_checker = HealthChecker()

def check_database():
    """Check database connectivity and basic operations"""
    try:
        from app.extensions import db
        
        start_time = time.time()
        # Simple connectivity test
        db.session.execute(text('SELECT 1'))
        db.session.commit()
        response_time = (time.time() - start_time) * 1000
        
        return {
            'healthy': True,
            'response_time_ms': round(response_time, 2),
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
    except SQLAlchemyError as e:
        return {
            'healthy': False,
            'error': f'Database error: {str(e)}',
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        return {
            'healthy': False,
            'error': f'Unexpected error: {str(e)}',
            'timestamp': datetime.now(timezone.utc).isoformat()
        }

def check_redis():
    """Check Redis connectivity and basic operations"""
    try:
        from app.extensions import redis_client
        
        start_time = time.time()
        # Test basic Redis operations
        test_key = 'health_check_test'
        redis_client.set(test_key, 'test_value', ex=10)
        value = redis_client.get(test_key)
        redis_client.delete(test_key)
        response_time = (time.time() - start_time) * 1000
        
        return {
            'healthy': True,
            'response_time_ms': round(response_time, 2),
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
    except redis.RedisError as e:
        return {
            'healthy': False,
            'error': f'Redis error: {str(e)}',
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        return {
            'healthy': False,
            'error': f'Unexpected error: {str(e)}',
            'timestamp': datetime.now(timezone.utc).isoformat()
        }

def check_system_resources():
    """Check system resource usage"""
    try:
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        # Define thresholds
        cpu_threshold = 90
        memory_threshold = 90
        disk_threshold = 90
        
        healthy = (
            cpu_percent < cpu_threshold and
            memory.percent < memory_threshold and
            disk.percent < disk_threshold
        )
        
        return {
            'healthy': healthy,
            'cpu_percent': cpu_percent,
            'memory_percent': memory.percent,
            'disk_percent': disk.percent,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        return {
            'healthy': False,
            'error': f'System check error: {str(e)}',
            'timestamp': datetime.now(timezone.utc).isoformat()
        }

def check_external_services():
    """Check external service dependencies"""
    external_services = []
    
    # Add external service URLs from environment
    openai_api = os.getenv('OPENAI_API_BASE_URL', 'https://api.openai.com')
    anthropic_api = os.getenv('ANTHROPIC_API_BASE_URL', 'https://api.anthropic.com')
    
    if openai_api:
        external_services.append(('OpenAI', openai_api))
    if anthropic_api:
        external_services.append(('Anthropic', anthropic_api))
    
    results = {}
    overall_healthy = True
    
    for service_name, url in external_services:
        try:
            start_time = time.time()
            response = requests.get(url, timeout=5)
            response_time = (time.time() - start_time) * 1000
            
            healthy = response.status_code < 500
            results[service_name.lower()] = {
                'healthy': healthy,
                'status_code': response.status_code,
                'response_time_ms': round(response_time, 2),
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            
            if not healthy:
                overall_healthy = False
                
        except requests.RequestException as e:
            results[service_name.lower()] = {
                'healthy': False,
                'error': str(e),
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            overall_healthy = False
    
    return {
        'healthy': overall_healthy,
        'services': results,
        'timestamp': datetime.now(timezone.utc).isoformat()
    }

# Register all health checks
health_checker.register_check('database', check_database)
health_checker.register_check('redis', check_redis)
health_checker.register_check('system', check_system_resources)
health_checker.register_check('external_services', check_external_services)

@health_bp.route('/health')
def health_check():
    """Comprehensive health check endpoint"""
    results = health_checker.run_all_checks()
    status_code = 200 if results['status'] == 'healthy' else 503
    return jsonify(results), status_code

@health_bp.route('/health/live')
def liveness_probe():
    """Kubernetes liveness probe - basic application responsiveness"""
    return jsonify({
        'status': 'alive',
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'uptime': time.time() - health_checker.start_time
    }), 200

@health_bp.route('/health/ready')
def readiness_probe():
    """Kubernetes readiness probe - application ready to serve traffic"""
    try:
        # Check critical dependencies
        db_result = check_database()
        redis_result = check_redis()
        
        ready = db_result.get('healthy', False) and redis_result.get('healthy', False)
        
        return jsonify({
            'status': 'ready' if ready else 'not_ready',
            'database': db_result,
            'redis': redis_result,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }), 200 if ready else 503
        
    except Exception as e:
        return jsonify({
            'status': 'not_ready',
            'error': str(e),
            'timestamp': datetime.now(timezone.utc).isoformat()
        }), 503

@health_bp.route('/health/metrics')
def health_metrics():
    """Detailed metrics for monitoring systems"""
    try:
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        # Database connection pool info
        from app.extensions import db
        pool = db.engine.pool
        
        return jsonify({
            'system': {
                'cpu_percent': cpu_percent,
                'memory_percent': memory.percent,
                'memory_available_mb': memory.available // (1024 * 1024),
                'disk_percent': disk.percent,
                'disk_free_gb': disk.free // (1024 * 1024 * 1024)
            },
            'database_pool': {
                'size': pool.size(),
                'checked_out': pool.checkedout(),
                'overflow': pool.overflow(),
                'checked_in': pool.checkedin()
            },
            'application': {
                'uptime': time.time() - health_checker.start_time,
                'flask_env': os.getenv('FLASK_ENV', 'development'),
                'python_version': os.sys.version
            },
            'timestamp': datetime.now(timezone.utc).isoformat()
        }), 200
        
    except Exception as e:
        return jsonify({
            'error': str(e),
            'timestamp': datetime.now(timezone.utc).isoformat()
        }), 500