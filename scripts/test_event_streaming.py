#!/usr/bin/env python3
"""
Event Streaming Demo

Demonstrates the event streaming capability of the Semantic Bus.
Shows how requesters can subscribe to execution events and receive
real-time progress updates from agents.

This example:
1. Starts the Semantic Bus with event streaming enabled
2. Registers a provider agent that emits progress events
3. Registers a requester agent that subscribes to events
4. Shows real-time event flow during execution

Usage:
    python scripts/test_event_streaming.py

Requirements:
    - OpenAI API key (OPENAI_API_KEY environment variable)
    - Or Ollama running locally with llama3.2 model
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from uuid import UUID, uuid4

# Add src to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.registry import AgentRegistry
from src.routing import IntentRouter
from src.session import SessionManager
from src.events import EventManager, EventType, EventSeverity
from src.events.models import (
    create_execution_started,
    create_execution_progress,
    create_execution_completed,
    create_flow_started,
    create_flow_agent_started,
    create_flow_agent_completed,
    create_reasoning_thought,
    create_agent_status,
)
from src.events.stream import EventFilter, create_session_filter

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ANSI colors for pretty output
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'


def print_event(event):
    """Pretty print an event."""
    event_type = event.event_type.value
    timestamp = event.timestamp.strftime("%H:%M:%S.%f")[:-3]
    
    # Color based on event type
    if event_type.startswith("execution."):
        color = Colors.GREEN
    elif event_type.startswith("flow."):
        color = Colors.BLUE
    elif event_type.startswith("reasoning."):
        color = Colors.CYAN
    elif event_type.startswith("agent."):
        color = Colors.YELLOW
    else:
        color = Colors.ENDC
    
    # Progress bar if available
    progress_bar = ""
    if event.progress_percent is not None:
        filled = int(event.progress_percent / 5)
        empty = 20 - filled
        progress_bar = f" [{Colors.GREEN}{'█' * filled}{Colors.ENDC}{'░' * empty}] {event.progress_percent:.0f}%"
    
    print(f"{Colors.BOLD}[{timestamp}]{Colors.ENDC} {color}{event_type}{Colors.ENDC}{progress_bar}")
    print(f"    {event.message}")
    
    if event.source_name:
        print(f"    {Colors.CYAN}Source: {event.source_name}{Colors.ENDC}")
    
    if event.data:
        print(f"    {Colors.YELLOW}Data: {json.dumps(event.data, default=str)}{Colors.ENDC}")
    
    print()


async def demo_event_publishing():
    """Demonstrate event publishing and subscription."""
    print(f"\n{Colors.HEADER}{'='*60}{Colors.ENDC}")
    print(f"{Colors.HEADER}   Event Streaming Demo{Colors.ENDC}")
    print(f"{Colors.HEADER}{'='*60}{Colors.ENDC}\n")
    
    # Create event manager
    event_manager = EventManager()
    
    # Create a mock session ID
    session_id = uuid4()
    tenant_id = "demo-tenant"
    exec_id = str(uuid4())
    flow_id = str(uuid4())
    
    print(f"Session ID: {session_id}")
    print(f"Execution ID: {exec_id}")
    print(f"Flow ID: {flow_id}\n")
    
    # Create a subscription for this session
    print(f"{Colors.BOLD}Creating event subscription...{Colors.ENDC}\n")
    subscription = await event_manager.subscribe_session(
        conn_id="demo-connection",
        session_id=session_id,
        include_history=True,
    )
    
    # Start a consumer task
    events_received = []
    
    async def consume_events():
        async for event in subscription:
            events_received.append(event)
            print_event(event)
    
    consumer_task = asyncio.create_task(consume_events())
    
    # Simulate a multi-agent flow with progress events
    print(f"{Colors.BOLD}Simulating multi-agent flow execution...{Colors.ENDC}\n")
    print(f"{Colors.HEADER}{'-'*60}{Colors.ENDC}\n")
    
    # Flow started
    await event_manager.publish_flow_started(
        session_id=session_id,
        tenant_id=tenant_id,
        flow_id=flow_id,
        flow_name="Translate and Summarize",
        agent_order=["translator", "summarizer"],
        exec_id=exec_id,
    )
    await asyncio.sleep(0.3)
    
    # Execution started
    await event_manager.publish_execution_started(
        session_id=session_id,
        tenant_id=tenant_id,
        exec_id=exec_id,
        action="translate_and_summarize",
        source_name="SemanticBus",
        total_steps=2,
    )
    await asyncio.sleep(0.3)
    
    # Agent 1: Translator starts
    translator_id = uuid4()
    await event_manager.publish_flow_agent_started(
        session_id=session_id,
        tenant_id=tenant_id,
        flow_id=flow_id,
        agent_id=translator_id,
        agent_name="translator",
        agent_index=0,
        agents_total=2,
        exec_id=exec_id,
    )
    await asyncio.sleep(0.2)
    
    # Translator reasoning/thinking
    await event_manager.publish_reasoning_thought(
        session_id=session_id,
        tenant_id=tenant_id,
        thought="Analyzing source text language... Detected: Spanish",
        step_number=1,
        source_agent_id=translator_id,
        source_name="translator",
        exec_id=exec_id,
    )
    await asyncio.sleep(0.3)
    
    # Translator progress
    await event_manager.publish_agent_status(
        session_id=session_id,
        tenant_id=tenant_id,
        agent_id=translator_id,
        agent_name="translator",
        status="translating",
        message="Translating from Spanish to English...",
        progress_percent=25.0,
        exec_id=exec_id,
    )
    await asyncio.sleep(0.4)
    
    await event_manager.publish_agent_status(
        session_id=session_id,
        tenant_id=tenant_id,
        agent_id=translator_id,
        agent_name="translator",
        status="translating",
        message="Processing 3 paragraphs...",
        progress_percent=40.0,
        exec_id=exec_id,
    )
    await asyncio.sleep(0.3)
    
    # Translator completed
    await event_manager.publish_flow_agent_completed(
        session_id=session_id,
        tenant_id=tenant_id,
        flow_id=flow_id,
        agent_id=translator_id,
        agent_name="translator",
        agent_index=0,
        agents_total=2,
        next_agent_name="summarizer",
        exec_id=exec_id,
    )
    await asyncio.sleep(0.3)
    
    # Agent 2: Summarizer starts
    summarizer_id = uuid4()
    await event_manager.publish_flow_agent_started(
        session_id=session_id,
        tenant_id=tenant_id,
        flow_id=flow_id,
        agent_id=summarizer_id,
        agent_name="summarizer",
        agent_index=1,
        agents_total=2,
        exec_id=exec_id,
    )
    await asyncio.sleep(0.2)
    
    # Summarizer reasoning
    await event_manager.publish_reasoning_thought(
        session_id=session_id,
        tenant_id=tenant_id,
        thought="Input received: 450 words of translated text. Creating concise summary...",
        step_number=1,
        source_agent_id=summarizer_id,
        source_name="summarizer",
        exec_id=exec_id,
    )
    await asyncio.sleep(0.3)
    
    # Summarizer progress
    await event_manager.publish_execution_progress(
        session_id=session_id,
        tenant_id=tenant_id,
        exec_id=exec_id,
        message="Extracting key points from translated text...",
        progress_percent=75.0,
        step_index=1,
        total_steps=2,
        source_agent_id=summarizer_id,
        source_name="summarizer",
    )
    await asyncio.sleep(0.4)
    
    await event_manager.publish_agent_status(
        session_id=session_id,
        tenant_id=tenant_id,
        agent_id=summarizer_id,
        agent_name="summarizer",
        status="summarizing",
        message="Generating final summary (target: 100 words)...",
        progress_percent=90.0,
        exec_id=exec_id,
    )
    await asyncio.sleep(0.3)
    
    # Summarizer completed
    await event_manager.publish_flow_agent_completed(
        session_id=session_id,
        tenant_id=tenant_id,
        flow_id=flow_id,
        agent_id=summarizer_id,
        agent_name="summarizer",
        agent_index=1,
        agents_total=2,
        exec_id=exec_id,
    )
    await asyncio.sleep(0.2)
    
    # Flow completed
    await event_manager.publish_flow_completed(
        session_id=session_id,
        tenant_id=tenant_id,
        flow_id=flow_id,
        flow_name="Translate and Summarize",
        agents_total=2,
        elapsed_ms=2500,
        exec_id=exec_id,
    )
    await asyncio.sleep(0.2)
    
    # Execution completed
    await event_manager.publish_execution_completed(
        session_id=session_id,
        tenant_id=tenant_id,
        exec_id=exec_id,
        message="Translation and summarization completed successfully!",
        elapsed_ms=2500,
        source_name="SemanticBus",
    )
    
    # Wait a bit for events to be processed
    await asyncio.sleep(0.5)
    
    # Cancel consumer and close subscription
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass
    
    await event_manager.close()
    
    # Summary
    print(f"\n{Colors.HEADER}{'-'*60}{Colors.ENDC}")
    print(f"\n{Colors.BOLD}Demo Complete!{Colors.ENDC}")
    print(f"Total events received: {len(events_received)}")
    
    # Event type breakdown
    type_counts = {}
    for event in events_received:
        type_str = event.event_type.value
        prefix = type_str.split('.')[0]
        type_counts[prefix] = type_counts.get(prefix, 0) + 1
    
    print(f"\nEvent breakdown:")
    for prefix, count in sorted(type_counts.items()):
        print(f"  - {prefix}.* : {count} events")


async def demo_event_filtering():
    """Demonstrate event filtering capabilities."""
    print(f"\n\n{Colors.HEADER}{'='*60}{Colors.ENDC}")
    print(f"{Colors.HEADER}   Event Filtering Demo{Colors.ENDC}")
    print(f"{Colors.HEADER}{'='*60}{Colors.ENDC}\n")
    
    event_manager = EventManager()
    session_id = uuid4()
    tenant_id = "demo-tenant"
    
    # Create filtered subscription (only flow events)
    flow_filter = EventFilter(
        session_ids={session_id},
        event_type_prefixes=["flow."],
    )
    
    subscription = await event_manager.subscribe_custom(
        conn_id="demo-filtered",
        filter=flow_filter,
        subscription_suffix="flow-only",
    )
    
    print(f"Created subscription with filter: flow.* events only\n")
    
    # Start consumer
    received = []
    
    async def consume():
        async for event in subscription:
            received.append(event)
    
    consumer = asyncio.create_task(consume())
    
    # Publish various event types
    print("Publishing events of different types...\n")
    
    # These should NOT be received (filtered out)
    await event_manager.publish_execution_started(
        session_id=session_id, tenant_id=tenant_id, exec_id="test",
        action="test", source_name="test"
    )
    await event_manager.publish_reasoning_thought(
        session_id=session_id, tenant_id=tenant_id, thought="thinking..."
    )
    await event_manager.publish_agent_status(
        session_id=session_id, tenant_id=tenant_id,
        agent_id=uuid4(), agent_name="test", status="running", message="msg"
    )
    
    # These SHOULD be received (match filter)
    await event_manager.publish_flow_started(
        session_id=session_id, tenant_id=tenant_id,
        flow_id="f1", flow_name="Test Flow", agent_order=["a", "b"]
    )
    await event_manager.publish_flow_agent_started(
        session_id=session_id, tenant_id=tenant_id,
        flow_id="f1", agent_id=uuid4(), agent_name="agent_a",
        agent_index=0, agents_total=2
    )
    await event_manager.publish_flow_completed(
        session_id=session_id, tenant_id=tenant_id,
        flow_id="f1", flow_name="Test Flow", agents_total=2
    )
    
    await asyncio.sleep(0.2)
    consumer.cancel()
    
    print(f"Events published: 6 total")
    print(f"Events received (filtered): {len(received)}")
    print(f"\n{Colors.GREEN}✓ Filter correctly passed only flow.* events!{Colors.ENDC}")
    
    for event in received:
        print(f"  - {event.event_type.value}: {event.message}")
    
    await event_manager.close()


async def main():
    """Run all demos."""
    try:
        await demo_event_publishing()
        await demo_event_filtering()
        
        print(f"\n\n{Colors.HEADER}{'='*60}{Colors.ENDC}")
        print(f"{Colors.GREEN}{Colors.BOLD}All demos completed successfully!{Colors.ENDC}")
        print(f"{Colors.HEADER}{'='*60}{Colors.ENDC}\n")
        
    except Exception as e:
        logger.exception(f"Demo failed: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
