"""
LLM Factory for Multi-Provider Support

Instantiates the appropriate LLM client based on model identifier and environment configuration.
Supports: OpenAI, Azure OpenAI, Anthropic, Google Gemini, and Ollama.
"""

import logging
import os
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class LLMConfig(BaseModel):
    """Configuration for LLM initialization."""

    model: str = Field(
        default="gpt-4o-mini",
        description="Model identifier (e.g., 'gpt-4o-mini', 'azure/gpt-4o', 'claude-sonnet-4-5-20250929')",
    )
    temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=2.0,
        description="Sampling temperature for model responses",
    )
    max_tokens: Optional[int] = Field(
        default=None,
        description="Maximum tokens to generate in response",
    )
    timeout: Optional[float] = Field(
        default=30.0,
        description="Request timeout in seconds",
    )
    max_retries: int = Field(
        default=2,
        description="Maximum number of retries on API errors",
    )


def create_llm(
    model: str = "gpt-4o-mini",
    temperature: float = 0.0,
    max_tokens: Optional[int] = None,
    timeout: Optional[float] = 30.0,
    max_retries: int = 2,
    **kwargs: Any,
) -> Any:
    """
    Create an LLM instance based on the model identifier.

    Model format examples:
    - OpenAI: "gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"
    - Azure OpenAI: "azure/gpt-4o-mini", "azure/gpt-4o"
    - Anthropic: "claude-sonnet-4-5-20250929", "claude-3-5-sonnet-20241022"
    - Google Gemini: "gemini/gemini-2.0-flash-exp", "gemini/gemini-1.5-pro"
    - Ollama: "ollama/llama3.2", "ollama/mistral"

    Environment variables required:
    - OpenAI: OPENAI_API_KEY
    - Azure OpenAI: AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_VERSION (optional)
    - Anthropic: ANTHROPIC_API_KEY
    - Google Gemini: GOOGLE_API_KEY
    - Ollama: No API key needed (runs locally)

    Args:
        model: Model identifier string
        temperature: Sampling temperature (0.0 to 2.0)
        max_tokens: Maximum tokens to generate (None for model default)
        timeout: Request timeout in seconds
        max_retries: Maximum number of retries on API errors
        **kwargs: Additional provider-specific parameters

    Returns:
        LLM instance (ChatOpenAI, AzureChatOpenAI, ChatAnthropic, etc.)

    Raises:
        ImportError: If required provider package is not installed
        ValueError: If required environment variables are missing

    Example:
        >>> # Create OpenAI LLM
        >>> llm = create_llm(model="gpt-4o-mini", temperature=0.7)
        >>>
        >>> # Create Azure OpenAI LLM
        >>> llm = create_llm(model="azure/gpt-4o", temperature=0.0)
        >>>
        >>> # Create with structured output
        >>> from pydantic import BaseModel
        >>> class Response(BaseModel):
        >>>     answer: str
        >>> llm_with_structure = create_llm(model="gpt-4o-mini").with_structured_output(Response)
    """
    try:
        # Azure OpenAI
        if model.startswith("azure/"):
            from langchain_openai import AzureChatOpenAI

            deployment_name = model.replace("azure/", "")

            # Get Azure-specific environment variables
            api_key = os.getenv("AZURE_OPENAI_API_KEY")
            endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
            api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")

            if not api_key:
                raise ValueError(
                    "Azure OpenAI requires AZURE_OPENAI_API_KEY environment variable"
                )
            if not endpoint:
                raise ValueError(
                    "Azure OpenAI requires AZURE_OPENAI_ENDPOINT environment variable"
                )

            logger.info(
                f"Creating Azure OpenAI LLM with deployment: {deployment_name}, endpoint: {endpoint}"
            )

            return AzureChatOpenAI(
                azure_deployment=deployment_name,
                azure_endpoint=endpoint,
                api_key=api_key,
                api_version=api_version,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                max_retries=max_retries,
                **kwargs,
            )

        # Ollama (local models)
        elif model.startswith("ollama/"):
            from langchain_ollama import ChatOllama

            model_name = model.replace("ollama/", "")

            logger.info(f"Creating Ollama LLM with model: {model_name}")

            return ChatOllama(
                model=model_name,
                temperature=temperature,
                num_predict=max_tokens,
                timeout=timeout,
                **kwargs,
            )

        # Google Gemini
        elif model.startswith("gemini/"):
            from langchain_google_genai import ChatGoogleGenerativeAI

            model_name = model.replace("gemini/", "")

            api_key = os.getenv("GOOGLE_API_KEY")
            if not api_key:
                raise ValueError(
                    "Google Gemini requires GOOGLE_API_KEY environment variable"
                )

            logger.info(f"Creating Google Gemini LLM with model: {model_name}")

            return ChatGoogleGenerativeAI(
                model=model_name,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                max_retries=max_retries,
                google_api_key=api_key,
                **kwargs,
            )

        # Anthropic Claude
        elif model.startswith("claude"):
            from langchain_anthropic import ChatAnthropic

            api_key = os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                raise ValueError(
                    "Anthropic requires ANTHROPIC_API_KEY environment variable"
                )

            logger.info(f"Creating Anthropic LLM with model: {model}")

            return ChatAnthropic(
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                max_retries=max_retries,
                anthropic_api_key=api_key,
                **kwargs,
            )

        # Default to OpenAI
        else:
            from langchain_openai import ChatOpenAI

            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise ValueError("OpenAI requires OPENAI_API_KEY environment variable")

            logger.info(f"Creating OpenAI LLM with model: {model}")

            return ChatOpenAI(
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                max_retries=max_retries,
                openai_api_key=api_key,
                **kwargs,
            )

    except ImportError as e:
        error_msg = (
            f"Failed to import required LangChain package: {e}\n\n"
            f"Installation instructions:\n"
            f"  - For Azure OpenAI: pip install langchain-openai\n"
            f"  - For OpenAI: pip install langchain-openai\n"
            f"  - For Anthropic: pip install langchain-anthropic\n"
            f"  - For Google Gemini: pip install langchain-google-genai\n"
            f"  - For Ollama: pip install langchain-ollama"
        )
        logger.error(error_msg)
        raise ImportError(error_msg) from e


def create_llm_from_env() -> Any:
    """
    Create an LLM instance using configuration from environment variables.

    Reads:
    - LIP_LLM_MODEL: Model identifier (default: "gpt-4o-mini")
    - LIP_LLM_TEMPERATURE: Temperature (default: 0.0)
    - LIP_LLM_MAX_TOKENS: Max tokens (optional)
    - LIP_LLM_TIMEOUT: Timeout in seconds (default: 30.0)
    - LIP_LLM_MAX_RETRIES: Max retries (default: 2)

    Returns:
        LLM instance configured from environment

    Example:
        >>> # In your .env file:
        >>> # LIP_LLM_MODEL=azure/gpt-4o
        >>> # LIP_LLM_TEMPERATURE=0.7
        >>>
        >>> llm = create_llm_from_env()
    """
    model = os.getenv("LIP_LLM_MODEL", "gpt-4o-mini")
    temperature = float(os.getenv("LIP_LLM_TEMPERATURE", "0.0"))
    max_tokens_str = os.getenv("LIP_LLM_MAX_TOKENS")
    max_tokens = int(max_tokens_str) if max_tokens_str else None
    timeout = float(os.getenv("LIP_LLM_TIMEOUT", "30.0"))
    max_retries = int(os.getenv("LIP_LLM_MAX_RETRIES", "2"))

    logger.info(
        f"Creating LLM from environment: model={model}, temperature={temperature}"
    )

    return create_llm(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        max_retries=max_retries,
    )
