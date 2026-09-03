import os
import json
import time
from typing import List, Dict, Any, Optional
from models.base_provider import BaseLLMProvider, LLMResponse

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

try:
    import boto3
    from botocore.exceptions import ClientError, BotoCoreError
    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False


# ─────────────────────────────────────────────────────────────────────────────
# Available Amazon Bedrock Models (ap-southeast-1 region)
# ─────────────────────────────────────────────────────────────────────────────
BEDROCK_MODELS = {
    # Anthropic Claude (best reasoning + code + tool use)
    "claude-sonnet-4.6":       "anthropic.claude-sonnet-4-6",
    "claude-sonnet-4.5":       "anthropic.claude-sonnet-4-5-20250929-v1:0",
    "claude-sonnet-4":         "anthropic.claude-sonnet-4-20250514-v1:0",
    "claude-opus-4.6":         "anthropic.claude-opus-4-6-v1",
    "claude-opus-5":           "anthropic.claude-opus-5",
    "claude-haiku-4.5":        "anthropic.claude-haiku-4-5-20251001-v1:0",
    "claude-haiku-3":          "anthropic.claude-3-haiku-20240307-v1:0",
    "claude-3.5-sonnet":       "anthropic.claude-3-5-sonnet-20241022-v2:0",
    "claude-fable-5":          "anthropic.claude-fable-5",

    # Amazon Nova (fast, cheap)
    "nova-pro":                "amazon.nova-pro-v1:0",
    "nova-lite":               "amazon.nova-lite-v1:0",
    "nova-micro":              "amazon.nova-micro-v1:0",
    "nova-2-lite":             "amazon.nova-2-lite-v1:0",

    # OpenAI on Bedrock
    "gpt-5.6-terra":           "openai.gpt-5.6-terra",
    "gpt-5.6-sol":             "openai.gpt-5.6-sol",
    "gpt-5.6-luna":            "openai.gpt-5.6-luna",

    # xAI Grok
    "grok-4.6":                "xai.grok-4.6",
}


class BedrockProvider(BaseLLMProvider):
    """
    Amazon Bedrock Multi-Model Provider for DOOM V2.
    Supports: Claude (Anthropic), Nova (Amazon), GPT-5.6 (OpenAI), Grok (xAI)
    Region: ap-southeast-1 (Singapore) — where the account has access.
    """
    name = "bedrock"

    def __init__(self):
        self.access_key = os.getenv("AWS_ACCESS_KEY_ID", "").strip()
        self.secret_key = os.getenv("AWS_SECRET_ACCESS_KEY", "").strip()
        self.region = os.getenv("AWS_BEDROCK_REGION", "ap-southeast-1").strip()

        # Primary: Claude Sonnet 4.6 | Fast: Claude Haiku 3 | Coding: Claude 3.5 Sonnet
        self.primary_model = os.getenv("BEDROCK_PRIMARY_MODEL", "anthropic.claude-3-5-sonnet-20241022-v2:0")
        self.fast_model = os.getenv("BEDROCK_FAST_MODEL", "anthropic.claude-3-haiku-20240307-v1:0")
        self.coding_model = os.getenv("BEDROCK_CODING_MODEL", "anthropic.claude-sonnet-4-6")

        self._client = None
        self._verified = None  # Cache availability result

    def _get_client(self):
        if self._client is None and BOTO3_AVAILABLE and self.access_key and self.secret_key:
            try:
                self._client = boto3.client(
                    "bedrock-runtime",
                    aws_access_key_id=self.access_key,
                    aws_secret_access_key=self.secret_key,
                    region_name=self.region
                )
            except Exception as e:
                print(f"[BEDROCK] Client init error: {e}")
        return self._client

    def is_available(self) -> bool:
        if not BOTO3_AVAILABLE:
            return False
        if not self.access_key or not self.secret_key:
            return False

        # Use cached availability result (re-check every 5 min)
        now = time.time()
        if self._verified is not None:
            cache_age = now - getattr(self, "_verified_at", 0)
            if cache_age < 300:  # 5-minute cache
                return self._verified

        try:
            import boto3 as b3
            from botocore.config import Config
            fast_cfg = Config(connect_timeout=1, read_timeout=2, retries={'max_attempts': 0})
            # Step 1: Verify credentials are valid
            sts = b3.client(
                "sts",
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_key,
                region_name=self.region,
                config=fast_cfg
            )
            sts.get_caller_identity()

            # Step 2: Test actual Bedrock inference (lightweight probe)
            rt = b3.client(
                "bedrock-runtime",
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_key,
                region_name=self.region,
                config=fast_cfg
            )
            probe_body = json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 5,
                "messages": [{"role": "user", "content": "Hi"}]
            })
            rt.invoke_model(
                modelId="anthropic.claude-3-haiku-20240307-v1:0",
                body=probe_body,
                contentType="application/json",
                accept="application/json"
            )
            self._verified = True
            self._verified_at = now
            print("[BEDROCK] [OK] Inference probe successful — Claude Haiku 3 is live.")
            return True

        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            msg = e.response.get("Error", {}).get("Message", "")
            if "being verified" in msg:
                print("[BEDROCK] Account pending AWS verification. Retrying in 5 min.")
            elif "ValidationException" in code or "AccessDenied" in code:
                print(f"[BEDROCK] Model inference not permitted yet: {msg[:80]}")
            self._verified = False
            self._verified_at = now
            return False
        except Exception as e:
            print(f"[BEDROCK] Availability check error: {e}")
            self._verified = False
            self._verified_at = now
            return False

    def _select_model_id(self, task_type: str) -> str:
        """Choose the best model for the task type."""
        if task_type in ["coding", "multi_step", "reasoning"]:
            return self.coding_model
        elif task_type in ["fast", "conversation"]:
            return self.fast_model
        return self.primary_model

    def _invoke_claude(self, client, model_id: str, prompt: str, system_prompt: str, tools: Optional[List[Dict]], temperature: float) -> LLMResponse:
        """Invokes Anthropic Claude models (all versions) via Bedrock."""
        messages = [{"role": "user", "content": prompt}]

        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1200,
            "temperature": temperature,
            "messages": messages
        }
        if system_prompt:
            request_body["system"] = system_prompt

        # Add tool calling schema if tools provided
        if tools:
            bedrock_tools = []
            for t in tools:
                fn = t.get("function", t)
                bedrock_tools.append({
                    "name": fn.get("name", ""),
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters", {"type": "object", "properties": {}, "required": []})
                })
            request_body["tools"] = bedrock_tools
            request_body["tool_choice"] = {"type": "auto"}

        resp = client.invoke_model(
            modelId=model_id,
            body=json.dumps(request_body),
            contentType="application/json",
            accept="application/json"
        )
        out = json.loads(resp["body"].read())

        text = ""
        tool_calls = []

        for block in out.get("content", []):
            if block.get("type") == "text":
                text += block.get("text", "")
            elif block.get("type") == "tool_use":
                tool_calls.append({
                    "id": block.get("id", ""),
                    "name": block.get("name", ""),
                    "arguments": block.get("input", {})
                })

        usage = out.get("usage", {})
        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            model_name=f"bedrock/{model_id}",
            usage={
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
            }
        )

    def _invoke_nova(self, client, model_id: str, prompt: str, system_prompt: str, temperature: float) -> LLMResponse:
        """Invokes Amazon Nova models via Bedrock."""
        messages = [{"role": "user", "content": [{"text": prompt}]}]
        if system_prompt:
            messages.insert(0, {"role": "user", "content": [{"text": f"System: {system_prompt}"}]})

        request_body = {
            "messages": messages,
            "inferenceConfig": {
                "maxTokens": 1000,
                "temperature": temperature
            }
        }

        resp = client.invoke_model(
            modelId=model_id,
            body=json.dumps(request_body),
            contentType="application/json",
            accept="application/json"
        )
        out = json.loads(resp["body"].read())

        text = ""
        for msg in out.get("output", {}).get("message", {}).get("content", []):
            text += msg.get("text", "")

        return LLMResponse(
            text=text,
            tool_calls=[],
            model_name=f"bedrock/{model_id}"
        )

    def _invoke_openai_bedrock(self, client, model_id: str, prompt: str, system_prompt: str, temperature: float) -> LLMResponse:
        """Invokes OpenAI GPT-5.6 models via Amazon Bedrock."""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        request_body = {
            "model": model_id,
            "messages": messages,
            "max_tokens": 1200,
            "temperature": temperature
        }

        resp = client.invoke_model(
            modelId=model_id,
            body=json.dumps(request_body),
            contentType="application/json",
            accept="application/json"
        )
        out = json.loads(resp["body"].read())
        text = out.get("choices", [{}])[0].get("message", {}).get("content", "")

        return LLMResponse(
            text=text,
            tool_calls=[],
            model_name=f"bedrock/{model_id}"
        )

    def generate(self,
                 prompt: str,
                 system_prompt: str = "",
                 tools: Optional[List[Dict[str, Any]]] = None,
                 temperature: float = 0.7,
                 task_type: str = "general") -> LLMResponse:
        """Main generation method — auto-selects best model and invocation format."""
        client = self._get_client()
        if not client:
            return LLMResponse(
                text="Amazon Bedrock is not available. Using fallback engine, Sujal.",
                tool_calls=[],
                model_name="bedrock/unavailable"
            )

        model_id = self._select_model_id(task_type)

        try:
            # Anthropic Claude family
            if model_id.startswith("anthropic."):
                return self._invoke_claude(client, model_id, prompt, system_prompt, tools, temperature)

            # Amazon Nova family
            elif model_id.startswith("amazon.nova"):
                return self._invoke_nova(client, model_id, prompt, system_prompt, temperature)

            # OpenAI GPT on Bedrock
            elif model_id.startswith("openai."):
                return self._invoke_openai_bedrock(client, model_id, prompt, system_prompt, temperature)

            # xAI Grok — try Claude format (similar API)
            elif model_id.startswith("xai."):
                return self._invoke_claude(client, model_id, prompt, system_prompt, tools, temperature)

            # Default: try Claude format as fallback
            else:
                return self._invoke_claude(client, model_id, prompt, system_prompt, tools, temperature)

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            error_msg = e.response.get("Error", {}).get("Message", str(e))

            if "AccessDeniedException" in error_code:
                if "being verified" in error_msg:
                    print(f"[BEDROCK] Account under AWS verification — will retry in 2 hours.")
                    return LLMResponse(
                        text="Amazon Bedrock is currently undergoing AWS account verification. This typically takes less than 2 hours. I'll fall back to the local engine for now, Sujal.",
                        tool_calls=[],
                        model_name="bedrock/pending_verification"
                    )
                print(f"[BEDROCK] Access denied for model {model_id}: {error_msg}")

            elif "ValidationException" in error_code:
                # Model needs to be enabled in AWS Console
                print(f"[BEDROCK] Model {model_id} not enabled. Enable it in AWS Console -> Bedrock -> Model Access.")

            print(f"[BEDROCK ERROR] {error_code}: {error_msg[:120]}")

            # Try fallback to fast model
            if model_id != self.fast_model:
                try:
                    return self._invoke_claude(client, self.fast_model, prompt, system_prompt, tools, temperature)
                except Exception:
                    pass

            return LLMResponse(
                text="",
                tool_calls=[],
                model_name=f"bedrock/error/{model_id}"
            )

        except Exception as e:
            print(f"[BEDROCK ERROR] Unexpected: {e}")
            return LLMResponse(
                text="",
                tool_calls=[],
                model_name=f"bedrock/error"
            )

    def list_available_models(self) -> Dict[str, str]:
        """Returns the catalog of known Bedrock models."""
        return BEDROCK_MODELS

    def get_status(self) -> Dict[str, Any]:
        """Returns full diagnostic status."""
        return {
            "available": self.is_available(),
            "region": self.region,
            "primary_model": self.primary_model,
            "fast_model": self.fast_model,
            "coding_model": self.coding_model,
            "account_id": "625867802280",
            "boto3_installed": BOTO3_AVAILABLE,
            "total_models_catalog": len(BEDROCK_MODELS)
        }
