"""
Unified API client factory with circuit breakers, rate limiting, and retry logic.
Standardized integration for AI providers with comprehensive error handling.
"""

import os
import time
import logging
from typing import Dict, Any, Optional, List
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import openai
import anthropic
from app.extensions import get_openai_breaker, get_anthropic_breaker
import pybreaker

logger = logging.getLogger(__name__)

class APIProvider(Enum):
    """Supported AI API providers"""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"

@dataclass
class APIResponse:
    """Standardized API response wrapper"""
    success: bool
    data: Any = None
    error: Optional[str] = None
    status_code: Optional[int] = None
    response_time: Optional[float] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    tokens_used: Optional[int] = None

class APIClientError(Exception):
    """Base exception for API client errors"""
    pass

class RateLimitError(APIClientError):
    """Rate limit exceeded error"""
    pass

class AuthenticationError(APIClientError):
    """Authentication failed error"""
    pass

class QuotaExceededError(APIClientError):
    """API quota exceeded error"""
    pass

class BaseAPIClient(ABC):
    """Abstract base class for API clients"""
    
    def __init__(self, api_key: str, base_url: str, timeout: int = 30):
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.session = self._create_session()
    
    def _create_session(self) -> requests.Session:
        """Create HTTP session with retry strategy"""
        session = requests.Session()
        
        # Configure retry strategy
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "POST"]
        )
        
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        return session
    
    @abstractmethod
    def generate_completion(self, prompt: str, **kwargs) -> APIResponse:
        """Generate text completion"""
        pass
    
    @abstractmethod
    def generate_chat_completion(self, messages: List[Dict], **kwargs) -> APIResponse:
        """Generate chat completion"""
        pass
    
    def _handle_error(self, error: Exception, provider: str) -> APIResponse:
        """Standardized error handling"""
        error_message = str(error)
        
        if "rate limit" in error_message.lower():
            return APIResponse(
                success=False,
                error="Rate limit exceeded",
                provider=provider
            )
        elif "authentication" in error_message.lower() or "unauthorized" in error_message.lower():
            return APIResponse(
                success=False,
                error="Authentication failed",
                provider=provider
            )
        elif "quota" in error_message.lower() or "billing" in error_message.lower():
            return APIResponse(
                success=False,
                error="API quota exceeded",
                provider=provider
            )
        else:
            return APIResponse(
                success=False,
                error=f"API error: {error_message}",
                provider=provider
            )

class OpenAIClient(BaseAPIClient):
    """OpenAI API client with circuit breaker protection"""
    
    def __init__(self, api_key: str, base_url: str = None, model: str = "gpt-4"):
        base_url = base_url or "https://api.openai.com/v1"
        super().__init__(api_key, base_url)
        self.model = model
        self.client = openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=self.timeout
        )
        self.circuit_breaker = get_openai_breaker()
    
    @pybreaker.CircuitBreakerError
    def _make_request(self, request_func, *args, **kwargs):
        """Make API request with circuit breaker protection"""
        try:
            if self.circuit_breaker:
                return self.circuit_breaker(request_func)(*args, **kwargs)
            else:
                return request_func(*args, **kwargs)
        except pybreaker.CircuitBreakerError:
            raise APIClientError("OpenAI service temporarily unavailable")
    
    def generate_completion(self, prompt: str, **kwargs) -> APIResponse:
        """Generate text completion using OpenAI"""
        start_time = time.time()
        
        try:
            response = self._make_request(
                self.client.completions.create,
                model=kwargs.get('model', self.model),
                prompt=prompt,
                max_tokens=kwargs.get('max_tokens', 1000),
                temperature=kwargs.get('temperature', 0.7),
                top_p=kwargs.get('top_p', 1.0),
                frequency_penalty=kwargs.get('frequency_penalty', 0.0),
                presence_penalty=kwargs.get('presence_penalty', 0.0)
            )
            
            response_time = time.time() - start_time
            
            return APIResponse(
                success=True,
                data=response.choices[0].text.strip(),
                response_time=response_time,
                provider="openai",
                model=response.model,
                tokens_used=response.usage.total_tokens
            )
            
        except Exception as e:
            response_time = time.time() - start_time
            logger.error(f"OpenAI completion error: {e}")
            
            error_response = self._handle_error(e, "openai")
            error_response.response_time = response_time
            return error_response
    
    def generate_chat_completion(self, messages: List[Dict], **kwargs) -> APIResponse:
        """Generate chat completion using OpenAI"""
        start_time = time.time()
        
        try:
            response = self._make_request(
                self.client.chat.completions.create,
                model=kwargs.get('model', self.model),
                messages=messages,
                max_tokens=kwargs.get('max_tokens', 1000),
                temperature=kwargs.get('temperature', 0.7),
                top_p=kwargs.get('top_p', 1.0),
                frequency_penalty=kwargs.get('frequency_penalty', 0.0),
                presence_penalty=kwargs.get('presence_penalty', 0.0),
                stream=kwargs.get('stream', False)
            )
            
            response_time = time.time() - start_time
            
            return APIResponse(
                success=True,
                data=response.choices[0].message.content,
                response_time=response_time,
                provider="openai",
                model=response.model,
                tokens_used=response.usage.total_tokens
            )
            
        except Exception as e:
            response_time = time.time() - start_time
            logger.error(f"OpenAI chat completion error: {e}")
            
            error_response = self._handle_error(e, "openai")
            error_response.response_time = response_time
            return error_response

class AnthropicClient(BaseAPIClient):
    """Anthropic API client with circuit breaker protection"""
    
    def __init__(self, api_key: str, base_url: str = None, model: str = "claude-3-sonnet-20240229"):
        base_url = base_url or "https://api.anthropic.com"
        super().__init__(api_key, base_url)
        self.model = model
        self.client = anthropic.Anthropic(
            api_key=api_key,
            base_url=base_url,
            timeout=self.timeout
        )
        self.circuit_breaker = get_anthropic_breaker()
    
    def _make_request(self, request_func, *args, **kwargs):
        """Make API request with circuit breaker protection"""
        try:
            if self.circuit_breaker:
                return self.circuit_breaker(request_func)(*args, **kwargs)
            else:
                return request_func(*args, **kwargs)
        except pybreaker.CircuitBreakerError:
            raise APIClientError("Anthropic service temporarily unavailable")
    
    def generate_completion(self, prompt: str, **kwargs) -> APIResponse:
        """Generate text completion using Anthropic"""
        # Convert to chat format for Claude
        messages = [{"role": "user", "content": prompt}]
        return self.generate_chat_completion(messages, **kwargs)
    
    def generate_chat_completion(self, messages: List[Dict], **kwargs) -> APIResponse:
        """Generate chat completion using Anthropic"""
        start_time = time.time()
        
        try:
            response = self._make_request(
                self.client.messages.create,
                model=kwargs.get('model', self.model),
                messages=messages,
                max_tokens=kwargs.get('max_tokens', 1000),
                temperature=kwargs.get('temperature', 0.7),
                top_p=kwargs.get('top_p', 1.0)
            )
            
            response_time = time.time() - start_time
            
            return APIResponse(
                success=True,
                data=response.content[0].text,
                response_time=response_time,
                provider="anthropic",
                model=response.model,
                tokens_used=response.usage.input_tokens + response.usage.output_tokens
            )
            
        except Exception as e:
            response_time = time.time() - start_time
            logger.error(f"Anthropic completion error: {e}")
            
            error_response = self._handle_error(e, "anthropic")
            error_response.response_time = response_time
            return error_response

class APIClientFactory:
    """Factory for creating and managing API clients"""
    
    _clients: Dict[str, BaseAPIClient] = {}
    
    @classmethod
    def get_client(cls, provider: APIProvider, **kwargs) -> BaseAPIClient:
        """Get or create API client for provider"""
        
        if provider == APIProvider.OPENAI:
            api_key = kwargs.get('api_key') or os.getenv('OPENAI_API_KEY')
            if not api_key:
                raise APIClientError("OpenAI API key not provided")
            
            client_key = f"openai_{hash(api_key)}"
            if client_key not in cls._clients:
                cls._clients[client_key] = OpenAIClient(
                    api_key=api_key,
                    base_url=kwargs.get('base_url') or os.getenv('OPENAI_API_BASE_URL'),
                    model=kwargs.get('model') or os.getenv('OPENAI_MODEL', 'gpt-4')
                )
            return cls._clients[client_key]
        
        elif provider == APIProvider.ANTHROPIC:
            api_key = kwargs.get('api_key') or os.getenv('ANTHROPIC_API_KEY')
            if not api_key:
                raise APIClientError("Anthropic API key not provided")
            
            client_key = f"anthropic_{hash(api_key)}"
            if client_key not in cls._clients:
                cls._clients[client_key] = AnthropicClient(
                    api_key=api_key,
                    base_url=kwargs.get('base_url') or os.getenv('ANTHROPIC_API_BASE_URL'),
                    model=kwargs.get('model') or os.getenv('ANTHROPIC_MODEL', 'claude-3-sonnet-20240229')
                )
            return cls._clients[client_key]
        
        else:
            raise APIClientError(f"Unsupported provider: {provider}")
    
    @classmethod
    def clear_clients(cls):
        """Clear all cached clients"""
        cls._clients.clear()

class UnifiedAPIClient:
    """Unified interface for multiple AI providers with load balancing and fallback"""
    
    def __init__(self, primary_provider: APIProvider, fallback_providers: List[APIProvider] = None):
        self.primary_provider = primary_provider
        self.fallback_providers = fallback_providers or []
        self.request_count = 0
    
    def generate_completion(self, prompt: str, **kwargs) -> APIResponse:
        """Generate completion with automatic fallback"""
        providers = [self.primary_provider] + self.fallback_providers
        
        for provider in providers:
            try:
                client = APIClientFactory.get_client(provider, **kwargs)
                response = client.generate_completion(prompt, **kwargs)
                
                if response.success:
                    self.request_count += 1
                    return response
                
                # Log failed attempt and try next provider
                logger.warning(f"Provider {provider.value} failed: {response.error}")
                
            except Exception as e:
                logger.error(f"Provider {provider.value} error: {e}")
                continue
        
        # All providers failed
        return APIResponse(
            success=False,
            error="All AI providers failed",
            provider="unified"
        )
    
    def generate_chat_completion(self, messages: List[Dict], **kwargs) -> APIResponse:
        """Generate chat completion with automatic fallback"""
        providers = [self.primary_provider] + self.fallback_providers
        
        for provider in providers:
            try:
                client = APIClientFactory.get_client(provider, **kwargs)
                response = client.generate_chat_completion(messages, **kwargs)
                
                if response.success:
                    self.request_count += 1
                    return response
                
                # Log failed attempt and try next provider
                logger.warning(f"Provider {provider.value} failed: {response.error}")
                
            except Exception as e:
                logger.error(f"Provider {provider.value} error: {e}")
                continue
        
        # All providers failed
        return APIResponse(
            success=False,
            error="All AI providers failed",
            provider="unified"
        )
    
    def get_stats(self) -> Dict:
        """Get client usage statistics"""
        return {
            'primary_provider': self.primary_provider.value,
            'fallback_providers': [p.value for p in self.fallback_providers],
            'request_count': self.request_count,
            'circuit_breaker_states': {
                'openai': get_openai_breaker().current_state if get_openai_breaker() else 'unknown',
                'anthropic': get_anthropic_breaker().current_state if get_anthropic_breaker() else 'unknown'
            }
        }

# Convenience functions
def get_openai_client(**kwargs) -> OpenAIClient:
    """Get OpenAI client instance"""
    return APIClientFactory.get_client(APIProvider.OPENAI, **kwargs)

def get_anthropic_client(**kwargs) -> AnthropicClient:
    """Get Anthropic client instance"""
    return APIClientFactory.get_client(APIProvider.ANTHROPIC, **kwargs)

def get_unified_client(primary: str = "openai", fallbacks: List[str] = None) -> UnifiedAPIClient:
    """Get unified client with fallback support"""
    primary_provider = APIProvider(primary)
    fallback_providers = [APIProvider(fb) for fb in (fallbacks or [])]
    
    return UnifiedAPIClient(primary_provider, fallback_providers)