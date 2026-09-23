"""
HTTP client for communicating with remote Intercept agents.
"""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger("intercept.agent_client")


class AgentHTTPError(RuntimeError):
    """Exception raised when agent HTTP request fails."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class AgentConnectionError(AgentHTTPError):
    """Exception raised when agent is unreachable."""

    pass


class AgentClient:
    """HTTP client for communicating with a remote Intercept agent."""

    def __init__(self, base_url: str, api_key: str | None = None, timeout: float = 60.0):
        """
        Initialize agent client.

        Args:
            base_url: Base URL of the agent (e.g., http://192.168.1.50:8020)
            api_key: Optional API key for authentication
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> dict:
        """Get request headers."""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    def _get(self, path: str, params: dict | None = None) -> dict:
        """
        Perform GET request to agent.

        Args:
            path: URL path (e.g., /capabilities)
            params: Optional query parameters

        Returns:
            Parsed JSON response

        Raises:
            AgentHTTPError: On HTTP errors
            AgentConnectionError: If agent is unreachable
        """
        url = f"{self.base_url}{path}"
        try:
            response = requests.get(url, headers=self._headers(), params=params, timeout=self.timeout)
            response.raise_for_status()
            return response.json() if response.content else {}
        except requests.ConnectionError as e:
            raise AgentConnectionError(f"Cannot connect to agent at {self.base_url}: {e}")
        except requests.Timeout:
            raise AgentConnectionError(f"Request to agent timed out after {self.timeout}s")
        except requests.HTTPError as e:
            # Try to extract error message from response body
            error_msg = f"Agent returned error: {e.response.status_code}"
            try:
                error_data = e.response.json()
                if "message" in error_data:
                    error_msg = error_data["message"]
                elif "error" in error_data:
                    error_msg = error_data["error"]
            except Exception:
                pass
            raise AgentHTTPError(error_msg, status_code=e.response.status_code)
        except requests.RequestException as e:
            raise AgentHTTPError(f"Request failed: {e}")

    def _post(self, path: str, data: dict | None = None, timeout: float | None = None) -> dict:
        """
        Perform POST request to agent.

        Args:
            path: URL path (e.g., /sensor/start)
            data: Optional JSON body

        Returns:
            Parsed JSON response

        Raises:
            AgentHTTPError: On HTTP errors
            AgentConnectionError: If agent is unreachable
        """
        url = f"{self.base_url}{path}"
        request_timeout = self.timeout if timeout is None else timeout
        try:
            response = requests.post(url, json=data or {}, headers=self._headers(), timeout=request_timeout)
            response.raise_for_status()
            return response.json() if response.content else {}
        except requests.ConnectionError as e:
            raise AgentConnectionError(f"Cannot connect to agent at {self.base_url}: {e}")
        except requests.Timeout:
            raise AgentConnectionError(f"Request to agent timed out after {request_timeout}s")
        except requests.HTTPError as e:
            # Try to extract error message from response body
            error_msg = f"Agent returned error: {e.response.status_code}"
            try:
                error_data = e.response.json()
                if "message" in error_data:
                    error_msg = error_data["message"]
                elif "error" in error_data:
                    error_msg = error_data["error"]
            except Exception:
                pass
            raise AgentHTTPError(error_msg, status_code=e.response.status_code)
        except requests.RequestException as e:
            raise AgentHTTPError(f"Request failed: {e}")

    def get(self, path: str, params: dict | None = None) -> dict:
        """Public GET method for arbitrary endpoints."""
        return self._get(path, params)

    def post(self, path: str, data: dict | None = None, timeout: float | None = None) -> dict:
        """Public POST method for arbitrary endpoints."""
        return self._post(path, data, timeout=timeout)

    # =========================================================================
    # Capability & Status
    # =========================================================================

    def get_capabilities(self) -> dict:
        """
        Get agent capabilities (available modes, devices).

        Returns:
            Dict with 'modes' (mode -> bool), 'devices' (list), 'agent_version'
        """
        return self._get("/capabilities")

    def get_status(self) -> dict:
        """
        Get agent status.

        Returns:
            Dict with 'running_modes', 'uptime', 'push_enabled', etc.
        """
        return self._get("/status")

    def health_check(self) -> bool:
        """
        Check if agent is healthy.

        Returns:
            True if agent is reachable and healthy
        """
        try:
            result = self._get("/health")
            return result.get("status") == "healthy"
        except (AgentHTTPError, AgentConnectionError):
            return False

    def get_config(self) -> dict:
        """Get agent configuration (non-sensitive fields)."""
        return self._get("/config")

    def update_config(self, **kwargs) -> dict:
        """
        Update agent configuration.

        Args:
            push_enabled: Enable/disable push mode
            push_interval: Push interval in seconds

        Returns:
            Updated config
        """
        return self._post("/config", kwargs)

    # =========================================================================
    # Mode Operations
    # =========================================================================

    def start_mode(self, mode: str, params: dict | None = None) -> dict:
        """
        Start a mode on the agent.

        Args:
            mode: Mode name (e.g., 'sensor', 'adsb', 'wifi')
            params: Mode-specific parameters

        Returns:
            Start result with 'status' field
        """
        return self._post(f"/{mode}/start", params or {})

    def stop_mode(self, mode: str, timeout: float = 8.0) -> dict:
        """
        Stop a running mode on the agent.

        Args:
            mode: Mode name

        Returns:
            Stop result with 'status' field
        """
        return self._post(f"/{mode}/stop", timeout=timeout)

    def get_mode_status(self, mode: str) -> dict:
        """
        Get status of a specific mode.

        Args:
            mode: Mode name

        Returns:
            Mode status with 'running' field
        """
        return self._get(f"/{mode}/status")

    def get_mode_data(self, mode: str) -> dict:
        """
        Get current data snapshot for a mode.

        Args:
            mode: Mode name

        Returns:
            Data snapshot with 'data' field
        """
        return self._get(f"/{mode}/data")

    # =========================================================================
    # Convenience Methods
    # =========================================================================

    def refresh_metadata(self) -> dict:
        """
        Fetch comprehensive metadata from agent.

        Returns:
            Dict with capabilities, status, and config
        """
        metadata = {
            "capabilities": None,
            "status": None,
            "config": None,
            "healthy": False,
        }

        try:
            metadata["capabilities"] = self.get_capabilities()
            metadata["status"] = self.get_status()
            metadata["config"] = self.get_config()
            metadata["healthy"] = True
        except (AgentHTTPError, AgentConnectionError) as e:
            logger.warning(f"Failed to refresh agent metadata: {e}")

        return metadata

    def __repr__(self) -> str:
        return f"AgentClient({self.base_url})"


def create_client_from_agent(agent: dict) -> AgentClient:
    """
    Create an AgentClient from an agent database record.

    Args:
        agent: Agent dict from database

    Returns:
        Configured AgentClient
    """
    # Agent dicts no longer carry the key (it is returned to browsers), so
    # fetch it here. A dict that still has one - a test fixture, say - wins.
    api_key = agent.get("api_key")
    if api_key is None and agent.get("id") is not None:
        from utils.database import get_agent_api_key

        api_key = get_agent_api_key(agent["id"])
    return AgentClient(base_url=agent["base_url"], api_key=api_key, timeout=60.0)
