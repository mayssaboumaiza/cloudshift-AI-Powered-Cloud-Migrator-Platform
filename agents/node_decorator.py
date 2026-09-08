"""
node_decorator.py - Decorators for automatic event publishing in graph nodes.

Wraps node functions to automatically publish phase_started, phase_progress, phase_completed events.

USAGE:
    from agents.node_decorator import publish_events

    # Wrap an existing node function
    @publish_events(phase="agent_01")
    def my_node(state: MigrationState) -> dict:
        # Your node logic here
        return {"result": ...}

    # In graph:
    workflow.add_node("my_node_name", my_node)
"""
import asyncio
import functools
import inspect
import logging
from typing import Callable, Any, Dict

from agents.checkpoint_events import EventPublisher
from agents.pipeline_state import MigrationState

logger = logging.getLogger("NodeDecorator")


def _run_async_from_sync(coro) -> Any:
    """Run a coroutine from sync code without reusing a running event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    # Fallback for contexts where a loop is already active in this thread.
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(lambda: asyncio.run(coro))
        return future.result(timeout=30)


def publish_events(phase: str):
    """
    Decorator to auto-publish phase events for a graph node.

    Publishes:
        - phase_started (immediately)
        - phase_progress (if state has 'progress' field)
        - phase_completed (on successful completion)
        - error (on exception)

    Args:
        phase: Phase name (e.g., 'agent_01', 'iac_parser')

    Usage:
        @publish_events(phase="agent_01")
        def my_node(state: MigrationState) -> dict:
            return {"result": ...}

    Example:
        @publish_events(phase="agent_02")
        async def generate_iac_node(state):
            pub = state.get("_event_publisher")  # Injected by decorator
            await pub.phase_progress("agent_02", progress=25)
            # ... generate IaC ...
            return {"iac_output": ...}
    """

    def decorator(func: Callable) -> Callable:
        is_async = inspect.iscoroutinefunction(func)

        if is_async:

            @functools.wraps(func)
            async def async_wrapper(state: MigrationState) -> Dict[str, Any]:
                thread_id = state.get("thread_id", "unknown")
                pub = EventPublisher(thread_id)

                try:
                    # Announce phase start
                    await pub.phase_started(
                        phase,
                        details={"function": func.__name__},
                    )

                    # Execute node
                    result = await func(state)

                    # Announce completion
                    if isinstance(result, dict):
                        # Get progress from result if available
                        progress = result.get("progress", 100)
                        await pub.phase_completed(
                            phase,
                            details={"result_keys": list(result.keys())},
                        )
                    else:
                        await pub.phase_completed(phase)

                    return result

                except Exception as e:
                    # Announce error
                    await pub.error(
                        phase,
                        str(e),
                        details={
                            "exception_type": type(e).__name__,
                            "function": func.__name__,
                        },
                    )
                    raise

            return async_wrapper

        else:
            # Synchronous version
            @functools.wraps(func)
            def sync_wrapper(state: MigrationState) -> Dict[str, Any]:
                thread_id = state.get("thread_id", "unknown")
                pub = EventPublisher(thread_id)

                try:
                    # Announce phase start
                    _run_async_from_sync(
                        pub.phase_started(
                            phase,
                            details={"function": func.__name__},
                        )
                    )

                    # Inject publisher into state
                    # (removed - no node uses _event_publisher, avoid state pollution - P1-4)

                    # Execute node
                    result = func(state)

                    # Announce completion
                    try:
                        if isinstance(result, dict):
                            _run_async_from_sync(
                                pub.phase_completed(
                                    phase,
                                    details={"result_keys": list(result.keys())},
                                )
                            )
                        else:
                            _run_async_from_sync(pub.phase_completed(phase))
                    except Exception as completion_err:
                        logger.warning(f"Could not publish phase_completed: {completion_err}")

                    return result

                except Exception as e:
                    try:
                        _run_async_from_sync(
                            pub.error(
                                phase,
                                str(e),
                                details={
                                    "exception_type": type(e).__name__,
                                    "function": func.__name__,
                                },
                            )
                        )
                    except Exception as pub_err:
                        logger.warning(f"Could not publish error event: {pub_err}")
                    raise

            return sync_wrapper

    return decorator


def track_progress(phase: str):
    """
    Simpler decorator for tracking phase progress (if node reports it).

    Node should return dict with 'progress' field (0-100).

    Usage:
        @track_progress(phase="agent_02")
        def my_node(state):
            return {"progress": 50, "result": ...}
    """

    def decorator(func: Callable) -> Callable:
        is_async = inspect.iscoroutinefunction(func)

        if is_async:

            @functools.wraps(func)
            async def async_wrapper(state: MigrationState) -> Dict[str, Any]:
                thread_id = state.get("thread_id", "unknown")
                pub = EventPublisher(thread_id)

                try:
                    result = await func(state)

                    # Extract progress from result
                    if isinstance(result, dict) and "progress" in result:
                        progress = result.get("progress", 100)
                        await pub.phase_progress(
                            phase,
                            progress=progress,
                            details=result.get("details", {}),
                        )

                    return result

                except Exception as e:
                    await pub.error(phase, str(e))
                    raise

            return async_wrapper

        else:
            @functools.wraps(func)
            def sync_wrapper(state: MigrationState) -> Dict[str, Any]:
                thread_id = state.get("thread_id", "unknown")
                import asyncio

                loop = asyncio.get_event_loop()
                pub = EventPublisher(thread_id)

                try:
                    result = func(state)

                    if isinstance(result, dict) and "progress" in result:
                        progress = result.get("progress", 100)
                        loop.run_until_complete(
                            pub.phase_progress(
                                phase,
                                progress=progress,
                                details=result.get("details", {}),
                            )
                        )

                    return result

                except Exception as e:
                    loop.run_until_complete(pub.error(phase, str(e)))
                    raise

            return sync_wrapper

    return decorator
