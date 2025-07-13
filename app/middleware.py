"""
Flask middleware for request/response logging, metrics collection, and API monitoring.
Provides comprehensive observability for API requests and responses.
"""

import time
import uuid
import json
from datetime import datetime, timezone
from flask import request, g, current_app
from functools import wraps
import logging

class RequestResponseMiddleware:
    """Middleware for logging and monitoring HTTP requests and responses"""
    
    def __init__(self, app=None):
        self.app = app
        if app is not None:
            self.init_app(app)
    
    def init_app(self, app):
        """Initialize middleware with Flask app"""
        app.before_request(self.before_request)
        app.after_request(self.after_request)
        app.teardown_appcontext(self.teardown_request)
        
        # Create middleware logger
        self.logger = logging.getLogger('middleware')
        
    def before_request(self):
        """Called before each request"""
        # Generate unique request ID
        g.request_id = str(uuid.uuid4())
        g.start_time = time.time()
        g.request_start = datetime.now(timezone.utc)
        
        # Log incoming request
        self._log_request()
        
        # Set request ID in response headers
        g.response_headers = {'X-Request-ID': g.request_id}
    
    def after_request(self, response):
        """Called after each request"""
        # Calculate request duration
        if hasattr(g, 'start_time'):
            g.duration = time.time() - g.start_time
        else:
            g.duration = 0
        
        # Add custom headers
        if hasattr(g, 'response_headers'):
            for key, value in g.response_headers.items():
                response.headers[key] = value
        
        # Add timing header
        response.headers['X-Response-Time'] = f"{g.duration:.3f}s"
        
        # Log response
        self._log_response(response)
        
        return response
    
    def teardown_request(self, exception=None):
        """Called when request context is torn down"""
        if exception:
            self._log_exception(exception)
    
    def _log_request(self):
        """Log incoming request details"""
        # Skip logging for health checks and static files
        if self._should_skip_logging():
            return
        
        request_data = {
            'event': 'request_started',
            'request_id': g.request_id,
            'timestamp': g.request_start.isoformat(),
            'method': request.method,
            'url': request.url,
            'path': request.path,
            'remote_addr': request.remote_addr,
            'user_agent': request.headers.get('User-Agent', ''),
            'content_type': request.headers.get('Content-Type', ''),
            'content_length': request.headers.get('Content-Length', 0),
        }
        
        # Add query parameters (excluding sensitive data)
        if request.args:
            filtered_args = self._filter_sensitive_data(dict(request.args))
            request_data['query_params'] = filtered_args
        
        # Add form data for POST requests (excluding sensitive data)
        if request.method in ['POST', 'PUT', 'PATCH'] and request.form:
            filtered_form = self._filter_sensitive_data(dict(request.form))
            request_data['form_data'] = filtered_form
        
        # Add JSON data (excluding sensitive data)
        if request.is_json and request.get_json(silent=True):
            filtered_json = self._filter_sensitive_data(request.get_json())
            request_data['json_data'] = filtered_json
        
        self.logger.info(json.dumps(request_data))
    
    def _log_response(self, response):
        """Log response details"""
        # Skip logging for health checks and static files
        if self._should_skip_logging():
            return
        
        response_data = {
            'event': 'request_completed',
            'request_id': g.request_id,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'status_code': response.status_code,
            'content_type': response.headers.get('Content-Type', ''),
            'content_length': response.headers.get('Content-Length', 0),
            'duration_ms': round(g.duration * 1000, 2),
            'method': request.method,
            'path': request.path,
        }
        
        # Log level based on status code
        if response.status_code >= 500:
            self.logger.error(json.dumps(response_data))
        elif response.status_code >= 400:
            self.logger.warning(json.dumps(response_data))
        else:
            self.logger.info(json.dumps(response_data))
    
    def _log_exception(self, exception):
        """Log unhandled exceptions"""
        exception_data = {
            'event': 'request_exception',
            'request_id': getattr(g, 'request_id', 'unknown'),
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'exception_type': type(exception).__name__,
            'exception_message': str(exception),
            'method': request.method,
            'path': request.path,
        }
        
        self.logger.error(json.dumps(exception_data), exc_info=True)
    
    def _should_skip_logging(self):
        """Determine if request should be skipped from logging"""
        skip_paths = [
            '/health',
            '/health/live',
            '/health/ready',
            '/health/metrics',
            '/static/',
            '/favicon.ico'
        ]
        
        return any(request.path.startswith(path) for path in skip_paths)
    
    def _filter_sensitive_data(self, data):
        """Filter sensitive data from request/response data"""
        if not isinstance(data, dict):
            return data
        
        sensitive_keys = [
            'password', 'token', 'secret', 'key', 'auth', 'authorization',
            'api_key', 'access_token', 'refresh_token', 'csrf_token',
            'credit_card', 'ssn', 'social_security'
        ]
        
        filtered_data = {}
        for key, value in data.items():
            if any(sensitive_key in key.lower() for sensitive_key in sensitive_keys):
                filtered_data[key] = '[FILTERED]'
            elif isinstance(value, dict):
                filtered_data[key] = self._filter_sensitive_data(value)
            else:
                filtered_data[key] = value
        
        return filtered_data

def api_metrics_decorator(func):
    """Decorator to collect API endpoint metrics"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        endpoint = request.endpoint or 'unknown'
        method = request.method
        
        try:
            result = func(*args, **kwargs)
            status_code = getattr(result, 'status_code', 200)
            
            # Log successful API call
            duration = time.time() - start_time
            current_app.logger.info(
                f"API_METRIC endpoint={endpoint} method={method} "
                f"status={status_code} duration={duration:.3f}s"
            )
            
            return result
            
        except Exception as e:
            # Log failed API call
            duration = time.time() - start_time
            current_app.logger.error(
                f"API_METRIC endpoint={endpoint} method={method} "
                f"status=500 duration={duration:.3f}s error={str(e)}"
            )
            raise
    
    return wrapper

def rate_limit_exceeded_handler(e):
    """Custom handler for rate limit exceeded errors"""
    response_data = {
        'error': 'Rate limit exceeded',
        'message': 'Too many requests. Please try again later.',
        'retry_after': e.retry_after,
        'request_id': getattr(g, 'request_id', 'unknown'),
        'timestamp': datetime.now(timezone.utc).isoformat()
    }
    
    # Log rate limit violation
    current_app.logger.warning(
        f"RATE_LIMIT_EXCEEDED path={request.path} "
        f"remote_addr={request.remote_addr} "
        f"retry_after={e.retry_after}"
    )
    
    from flask import jsonify
    response = jsonify(response_data)
    response.status_code = 429
    response.headers['Retry-After'] = str(e.retry_after)
    return response

class APIVersionMiddleware:
    """Middleware to handle API versioning"""
    
    def __init__(self, app=None, default_version='v1'):
        self.app = app
        self.default_version = default_version
        if app is not None:
            self.init_app(app)
    
    def init_app(self, app):
        """Initialize API versioning middleware"""
        app.before_request(self.before_request)
    
    def before_request(self):
        """Extract and validate API version from request"""
        # Try to get version from header
        api_version = request.headers.get('API-Version')
        
        # Try to get version from URL path
        if not api_version and request.path.startswith('/api/'):
            path_parts = request.path.split('/')
            if len(path_parts) > 2 and path_parts[2].startswith('v'):
                api_version = path_parts[2]
        
        # Use default version if none specified
        if not api_version:
            api_version = self.default_version
        
        # Store version in request context
        g.api_version = api_version
        
        # Validate version
        supported_versions = ['v1', 'v2']  # Add your supported versions
        if api_version not in supported_versions:
            from flask import jsonify, abort
            current_app.logger.warning(
                f"Unsupported API version requested: {api_version}"
            )
            abort(400, description=f"Unsupported API version: {api_version}")

def init_middleware(app):
    """Initialize all middleware components"""
    
    # Initialize request/response middleware
    RequestResponseMiddleware(app)
    
    # Initialize API versioning middleware
    APIVersionMiddleware(app)
    
    # Register rate limit error handler
    from flask_limiter.errors import RateLimitExceeded
    app.register_error_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    
    current_app.logger.info("All middleware components initialized")