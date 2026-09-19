"""Resolve the process-owned country executor.

The worker country comes from immutable process configuration, never from a
job-controlled class name.
"""

from ..country_guard import configured_worker_target
from ..workflow import FastComputerUseAgent
from .br import BrazilComputerUseAgent
from .india import IndiaComputerUseAgent
from .mx import MexicoComputerUseAgent


EXECUTORS = {
    "CN": FastComputerUseAgent,
    "MX": MexicoComputerUseAgent,
    "BR": BrazilComputerUseAgent,
    "IN": IndiaComputerUseAgent,
}


def executor_class_for_worker():
    country, version = configured_worker_target()
    executor_class = EXECUTORS[country]
    expected = getattr(executor_class, "EXECUTOR_VERSION", "cn-legacy")
    if version != expected:
        raise ValueError(
            f"Worker 执行版本 {version} 与 {country} 执行器 {expected} 不匹配"
        )
    return executor_class


def executor_metadata():
    country, version = configured_worker_target()
    executor_class = executor_class_for_worker()
    metadata = getattr(executor_class, "executor_metadata", None)
    if callable(metadata):
        return metadata()
    return {
        "countryCode": country,
        "executorVersion": version,
        "sourceLocales": ["zh-CN", "en-US"],
        "executionRules": ["frozen-china-path"],
    }
