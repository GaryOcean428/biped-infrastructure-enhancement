"""
Enhanced Flask application factory with production-grade infrastructure.
Integrates all Phase 2-4 enhancements: monitoring, rate limiting, circuit breakers, and database optimization.
"""

import os
from flask import Flask
from config.production import get_config
from app.extensions import init_extensions
from app.middleware import init_middleware
from app.database import init_database, setup_database_events
from app.health import health_bp

def create_app(config_name=None):
    """
    Application factory with enhanced infrastructure
    
    Args:
        config_name: Configuration environment name
        
    Returns:
        Flask application instance with all enhancements
    """
    
    # Create Flask application
    app = Flask(__name__)
    
    # Load configuration
    if config_name is None:
        config_name = os.getenv('FLASK_ENV', 'development')
    
    config_class = get_config()
    app.config.from_object(config_class)
    
    # Initialize configuration
    config_class.init_app(app)
    
    # Initialize database configuration
    init_database(app)
    
    # Initialize all extensions (Sentry, Redis, Rate Limiter, Circuit Breakers, Logging)
    init_extensions(app)
    
    # Initialize middleware (Request/Response logging, API versioning, Metrics)
    init_middleware(app)
    
    # Setup database event listeners for monitoring
    from app.extensions import db
    setup_database_events(db)
    
    # Register blueprints
    register_blueprints(app)
    
    # Register error handlers
    register_error_handlers(app)
    
    # Setup CLI commands
    register_cli_commands(app)
    
    app.logger.info(f"Biped application created with {config_name} configuration")
    
    return app

def register_blueprints(app):
    """Register application blueprints"""
    
    # Register health check blueprint
    app.register_blueprint(health_bp, url_prefix='/api')
    
    # Register existing application blueprints
    # Note: Import existing blueprints from the current codebase
    try:
        # Example imports - adjust based on actual codebase structure
        from app.routes.main import main_bp
        from app.routes.api import api_bp
        from app.routes.auth import auth_bp
        
        app.register_blueprint(main_bp)
        app.register_blueprint(api_bp, url_prefix='/api/v1')
        app.register_blueprint(auth_bp, url_prefix='/auth')
        
    except ImportError as e:
        app.logger.warning(f"Could not import existing blueprints: {e}")
        # Create a simple test route for demonstration
        @app.route('/')
        def index():
            return {
                'message': 'Biped API with Enhanced Infrastructure',
                'version': 'v1.0.0',
                'status': 'operational'
            }

def register_error_handlers(app):
    """Register global error handlers"""
    
    @app.errorhandler(404)
    def not_found(error):
        return {
            'error': 'Not Found',
            'message': 'The requested resource was not found',
            'status_code': 404
        }, 404
    
    @app.errorhandler(500)
    def internal_error(error):
        from app.extensions import db
        db.session.rollback()
        
        app.logger.error(f"Internal server error: {error}")
        
        return {
            'error': 'Internal Server Error',
            'message': 'An unexpected error occurred',
            'status_code': 500
        }, 500
    
    @app.errorhandler(429)
    def rate_limit_handler(error):
        return {
            'error': 'Rate Limit Exceeded',
            'message': 'Too many requests. Please try again later.',
            'status_code': 429
        }, 429

def register_cli_commands(app):
    """Register CLI commands for database and maintenance"""
    
    @app.cli.command()
    def init_db():
        """Initialize the database"""
        from app.extensions import db
        db.create_all()
        app.logger.info("Database initialized")
    
    @app.cli.command()
    def test_connections():
        """Test all external connections"""
        from app.health import health_checker
        results = health_checker.run_all_checks()
        
        print("Connection Test Results:")
        print("=" * 50)
        for check_name, result in results['checks'].items():
            status = "✓ PASS" if result.get('healthy', False) else "✗ FAIL"
            print(f"{check_name}: {status}")
            if not result.get('healthy', False):
                print(f"  Error: {result.get('error', 'Unknown error')}")
        
        print(f"\nOverall Status: {results['status'].upper()}")
    
    @app.cli.command()
    def clear_cache():
        """Clear Redis cache"""
        from app.database import get_cache_manager
        cache_manager = get_cache_manager()
        
        if cache_manager:
            # Clear all cache keys with the app prefix
            cleared = cache_manager.invalidate_pattern("*")
            print(f"Cleared {cleared} cache entries")
        else:
            print("Cache manager not available")
    
    @app.cli.command()
    def show_circuit_breakers():
        """Show circuit breaker status"""
        from app.extensions import get_openai_breaker, get_anthropic_breaker, get_database_breaker
        
        breakers = {
            'OpenAI': get_openai_breaker(),
            'Anthropic': get_anthropic_breaker(),
            'Database': get_database_breaker()
        }
        
        print("Circuit Breaker Status:")
        print("=" * 50)
        
        for name, breaker in breakers.items():
            if breaker:
                print(f"{name}: {breaker.current_state}")
                print(f"  Failures: {breaker.fail_counter}")
                print(f"  Last failure: {getattr(breaker, 'last_failure_time', 'Never')}")
            else:
                print(f"{name}: Not initialized")