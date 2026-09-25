"""结构化日志：JSON lines 输出到 stdout。

要求（任务书 §4）：每行带 `request_id`、`user_id`、`method`、`path`、
`status`、`duration_ms`。systemd 下由 journald 收集；
`SyslogIdentifier=localcraft` 便于 `journalctl -t localcraft` 精确过滤。
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from app.core.config import settings

#: 业务字段，附加在 `extra={...}` 里
_EXTRA_FIELDS = (
    "request_id",
    "user_id",
    "method",
    "path",
    "status",
    "duration_ms",
    "event",
)

_RESERVED = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "asctime",
    }
)


class JsonFormatter(logging.Formatter):
    """把日志记录序列化成单行 JSON。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).strftime(
                "%Y-%m-%dT%H:%M:%S.%f"
            )[:-3]
            + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for field in _EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value

        # 其余自定义字段一并带上，方便排障
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key not in payload and not key.startswith("_"):
                try:
                    json.dumps(value)
                except (TypeError, ValueError):
                    value = repr(value)
                payload[key] = value

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


#: 标记我们自己安装的 handler。`configure_logging()` 只清理带这个标记的，
#: 不动宿主进程（pytest 的 caplog、gunicorn、被嵌入调用等）已装的 handler。
_OWN_HANDLER_FLAG = "_localcraft_stdout_handler"


def configure_logging() -> None:
    """装配 stdout 结构化日志。重复调用幂等。

    注意：**只移除本模块自己安装过的 handler**。早先的实现是
    `for h in list(root.handlers): root.removeHandler(h)`，那会把宿主进程
    已经装好的 handler 一并摘掉 —— 最典型的后果是 pytest 的 `caplog`
    静默失效（测试里 `caplog.records` 永远为空），以及被嵌入运行时
    日志凭空消失。这类副作用很难定位，所以这里改成带标记的精确清理。
    """
    root = logging.getLogger()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root.setLevel(level)

    for handler in list(root.handlers):
        if getattr(handler, _OWN_HANDLER_FLAG, False):
            root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.setLevel(level)
    setattr(handler, _OWN_HANDLER_FLAG, True)
    root.addHandler(handler)

    # uvicorn 自带的 access 日志与我们的请求日志重复，降到 WARNING。
    # 只清 uvicorn 自己的 logger，避免同一行日志被打印两次。
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
