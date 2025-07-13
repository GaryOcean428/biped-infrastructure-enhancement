"""
Example integration showing how to use the enhanced infrastructure in Flask routes.
Demonstrates rate limiting, circuit breakers, caching, and API clients.
"""

from flask import Blueprint, request, jsonify, g
from app.extensions import limiter, redis_client
from app.api_clients import get_unified_client, APIProvider
from app.database import get_cache_manager, QueryCache
from app.middleware import api_metrics_decorator
import logging

# Create example blueprint
example_bp = Blueprint('example', __name__)
logger = logging.getLogger(__name__)

@example_bp.route('/ai/chat', methods=['POST'])
@limiter.limit("10 per minute")  # Rate limiting
@api_metrics_decorator  # API metrics collection
def ai_chat():
    """
    Example AI chat endpoint with circuit breaker protection and caching
    """
    try:
        data = request.get_json()
        if not data or 'message' not in data:
            return jsonify({'error': 'Message is required'}), 400
        
        message = data['message']
        provider = data.get('provider', 'openai')
        
        # Create unified client with fallback
        client = get_unified_client(
            primary=provider,
            fallbacks=['anthropic'] if provider == 'openai' else ['openai']
        )
        
        # Check cache first
        cache_manager = get_cache_manager()
        cache_key = f"chat:{hash(message)}"
        
        if cache_manager:
            cached_response = cache_manager.get(cache_key)
            if cached_response:
                logger.info("Returning cached AI response")
                return jsonify({
                    'response': cached_response,
                    'cached': True,
                    'provider': 'cache'
                })
        
        # Generate AI response with circuit breaker protection
        messages = [{'role': 'user', 'content': message}]
        api_response = client.generate_chat_completion(
            messages=messages,
            max_tokens=1000,
            temperature=0.7
        )
        
        if api_response.success:
            # Cache successful response
            if cache_manager:
                cache_manager.set(cache_key, api_response.data, ttl=3600)
            
            return jsonify({
                'response': api_response.data,
                'cached': False,
                'provider': api_response.provider,
                'model': api_response.model,
                'tokens_used': api_response.tokens_used,
                'response_time': api_response.response_time
            })
        else:
            logger.error(f"AI API error: {api_response.error}")
            return jsonify({
                'error': 'AI service unavailable',
                'details': api_response.error
            }), 503
    
    except Exception as e:
        logger.error(f"Chat endpoint error: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@example_bp.route('/data/users', methods=['GET'])
@limiter.limit("100 per minute")
@api_metrics_decorator
@QueryCache(ttl=1800, key_prefix="users")  # Cache for 30 minutes
def get_users():
    """
    Example database endpoint with caching and monitoring
    """
    try:
        from app.extensions import db
        from app.database import get_db_monitor
        
        # This would be your actual User model
        # users = User.query.all()
        
        # Simulated database query for demonstration
        users_data = [
            {'id': 1, 'name': 'John Doe', 'email': 'john@example.com'},
            {'id': 2, 'name': 'Jane Smith', 'email': 'jane@example.com'}
        ]
        
        # Record query in monitoring
        monitor = get_db_monitor()
        monitor.record_query("SELECT * FROM users", 0.05, success=True)
        
        return jsonify({
            'users': users_data,
            'count': len(users_data),
            'cached': False
        })
    
    except Exception as e:
        logger.error(f"Users endpoint error: {e}")
        return jsonify({'error': 'Database error'}), 500

@example_bp.route('/admin/stats', methods=['GET'])
@limiter.limit("5 per minute")  # Stricter rate limiting for admin endpoints
@api_metrics_decorator
def admin_stats():
    """
    Example admin endpoint showing system statistics
    """
    try:
        from app.database import get_db_monitor
        from app.api_clients import APIClientFactory
        
        # Get database statistics
        db_monitor = get_db_monitor()
        db_stats = db_monitor.get_stats()
        
        # Get API client statistics
        unified_client = get_unified_client()
        api_stats = unified_client.get_stats()
        
        # Get cache statistics
        cache_stats = {}
        if redis_client:
            try:
                info = redis_client.info()
                cache_stats = {
                    'connected_clients': info.get('connected_clients', 0),
                    'used_memory_human': info.get('used_memory_human', '0B'),
                    'keyspace_hits': info.get('keyspace_hits', 0),
                    'keyspace_misses': info.get('keyspace_misses', 0)
                }
            except Exception as e:
                cache_stats = {'error': str(e)}
        
        return jsonify({
            'database': db_stats,
            'api_clients': api_stats,
            'cache': cache_stats,
            'request_id': g.get('request_id', 'unknown')
        })
    
    except Exception as e:
        logger.error(f"Admin stats error: {e}")
        return jsonify({'error': 'Failed to retrieve statistics'}), 500

@example_bp.route('/test/circuit-breaker', methods=['POST'])
@limiter.limit("5 per minute")
@api_metrics_decorator
def test_circuit_breaker():
    """
    Example endpoint to test circuit breaker functionality
    """
    try:
        data = request.get_json()
        provider = data.get('provider', 'openai')
        
        # Force a test of the circuit breaker
        if provider == 'openai':
            from app.extensions import get_openai_breaker
            breaker = get_openai_breaker()
        elif provider == 'anthropic':
            from app.extensions import get_anthropic_breaker
            breaker = get_anthropic_breaker()
        else:
            return jsonify({'error': 'Invalid provider'}), 400
        
        if breaker:
            return jsonify({
                'provider': provider,
                'state': str(breaker.current_state),
                'fail_counter': breaker.fail_counter,
                'success_counter': getattr(breaker, 'success_counter', 0)
            })
        else:
            return jsonify({'error': 'Circuit breaker not initialized'}), 500
    
    except Exception as e:
        logger.error(f"Circuit breaker test error: {e}")
        return jsonify({'error': 'Test failed'}), 500

@example_bp.route('/cache/test', methods=['POST'])
@limiter.limit("20 per minute")
@api_metrics_decorator
def test_cache():
    """
    Example endpoint to test caching functionality
    """
    try:
        data = request.get_json()
        key = data.get('key', 'test_key')
        value = data.get('value', 'test_value')
        ttl = data.get('ttl', 300)
        
        cache_manager = get_cache_manager()
        if not cache_manager:
            return jsonify({'error': 'Cache not available'}), 503
        
        # Set value in cache
        success = cache_manager.set(key, value, ttl)
        
        if success:
            # Retrieve value to verify
            cached_value = cache_manager.get(key)
            return jsonify({
                'success': True,
                'key': key,
                'value': cached_value,
                'ttl': ttl
            })
        else:
            return jsonify({'error': 'Failed to set cache value'}), 500
    
    except Exception as e:
        logger.error(f"Cache test error: {e}")
        return jsonify({'error': 'Cache test failed'}), 500

# Error handlers specific to this blueprint
@example_bp.errorhandler(429)
def handle_rate_limit(e):
    """Handle rate limit errors for this blueprint"""
    return jsonify({
        'error': 'Rate limit exceeded',
        'message': 'Too many requests to this endpoint',
        'retry_after': e.retry_after
    }), 429

@example_bp.errorhandler(503)
def handle_service_unavailable(e):
    """Handle service unavailable errors"""
    return jsonify({
        'error': 'Service temporarily unavailable',
        'message': 'External service is down or circuit breaker is open'
    }), 503