"""串行、零重试的模型调用与逐请求费用账本。"""
from __future__ import annotations
import hashlib
import json
import math
import time
from pathlib import Path

import httpx

MODELS = {'generator': 'deepseek-flash', 'judge': 'deepseek-v4-pro'}
PRICES = {'deepseek-flash': (0.3, 1.2), 'deepseek-v4-pro': (1.32, 3.96)}
MAX_INPUT_BYTES = 32768
MAX_OUTPUT = 1024


class BudgetExceeded(RuntimeError):
    pass


class BoundedChat:
    def __init__(self, api_key: str, *, max_calls: int, max_usd: float, ledger: Path,
                 transport: httpx.AsyncBaseTransport | None = None):
        if not 1 <= max_calls <= 960 or not math.isfinite(max_usd) or max_usd <= 0:
            raise ValueError('调用次数和费用上限必须有效')
        if ledger.exists():
            raise FileExistsError('不能覆盖旧调用账本')
        ledger.parent.mkdir(parents=True, exist_ok=True)
        self.max_calls, self.max_usd, self.ledger = max_calls, max_usd, ledger
        self.calls, self.accounted, self.stopped = 0, 0.0, False
        self.client = httpx.AsyncClient(base_url='https://api.deepseek.com',
            headers={'Authorization': 'Bearer ' + api_key}, timeout=30, trust_env=False,
            follow_redirects=False, transport=transport or httpx.AsyncHTTPTransport(local_address='0.0.0.0', retries=0))

    def _record(self, item: dict) -> None:
        with self.ledger.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(item, ensure_ascii=False) + '\n'); handle.flush()

    async def complete(self, *, model: str, system: str, user: str, tag: str) -> str:
        if model not in PRICES or self.stopped:
            raise BudgetExceeded('调用已停止或模型未授权')
        messages = [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]
        serialized = json.dumps(messages, ensure_ascii=False, separators=(',', ':')).encode()
        if len(serialized) > MAX_INPUT_BYTES:
            self.stopped = True
            raise ValueError('请求超过固定输入大小上限')
        # UTF-8 字节数 + framing 余量保守预留；不是供应商的最终账单。
        input_limit = len(serialized) + 1024
        input_price, output_price = PRICES[model]
        reserved = (input_limit * input_price + MAX_OUTPUT * output_price) / 1e6
        if self.calls >= self.max_calls or self.accounted + reserved > self.max_usd:
            self.stopped = True
            raise BudgetExceeded('发出请求前触发次数或费用上限')
        self.calls += 1; self.accounted += reserved
        event = {'attempt': self.calls, 'tag': tag, 'model': model, 'inputSha256': hashlib.sha256(serialized).hexdigest(),
                 'inputBytes': len(serialized), 'reservedUsd': reserved}
        self._record({**event, 'status': 'attempt'})  # 网络之前持久化；崩溃时保留未决预留
        started = time.monotonic()
        try:
            response = await self.client.post('/chat/completions', json={
                'model': model, 'messages': messages, 'temperature': 0, 'max_tokens': MAX_OUTPUT,
                'stream': False, 'response_format': {'type': 'json_object'}, 'thinking': {'type': 'disabled'},
            })
            response.raise_for_status()
            result = response.json()
            choice = result['choices'][0]
            content = choice['message']['content']
            if not isinstance(content, str) or choice['finish_reason'] != 'stop':
                raise ValueError('模型输出为空或因 token 上限截断')
            usage = result.get('usage', {})
            ins, outs = usage.get('prompt_tokens'), usage.get('completion_tokens')
            valid_usage = all(type(n) is int and n > 0 for n in [ins, outs])
            charged = (ins * input_price + outs * output_price) / 1e6 if valid_usage else reserved
            self.accounted += charged - reserved
            self._record({**event, 'status': 'completed', 'latencyMs': round((time.monotonic()-started)*1000, 2),
                          'accountedUsd': charged, 'usage': {'inputTokens': ins, 'outputTokens': outs},
                          'content': content})  # 只存可见答案/判分理由，不存 Header 或 reasoning_content
            if self.accounted > self.max_usd:
                self.stopped = True
                raise BudgetExceeded('返回用量超过预留；停止后续调用')
            return content
        except Exception as exc:
            self.stopped = True
            self._record({**event, 'status': 'failed', 'errorType': type(exc).__name__,
                          'latencyMs': round((time.monotonic()-started)*1000, 2)})
            raise

    async def close(self) -> None:
        await self.client.aclose()
