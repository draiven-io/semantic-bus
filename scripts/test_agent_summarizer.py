#!/usr/bin/env python3
"""
Test Agent - Summarizer Provider

This agent provides text summarization capabilities:
1. Registers with the bus with "summarize" capability
2. Waits for intent.deliver messages
3. Sends intent.offer with supported schemas
4. Receives session.created when requester agrees
5. Receives exec.request with text to summarize
6. Sends exec.result with the summarized text

Usage:
    python scripts/test_agent_summarizer.py

Run this BEFORE test_agent_intent_summarization.py to be available for orchestration.
This agent works alongside test_agent_translator.py for multi-agent orchestration demos.
"""

import asyncio
import json
import sys
from uuid import uuid4

import websockets

# Agent configuration
AGENT_ID = uuid4()
TENANT_ID = "test-tenant"
BUS_URL = "ws://localhost:8000/ws"

def create_envelope(
    message_type: str,
    payload: dict = None,
    ephemeral_interface_id: str = None,
    schema_id: str = None,
    correlation_id: str = None,
    causation_id: str = None
) -> str:
    """Create a message envelope."""
    envelope = {
        "message_id": str(uuid4()),
        "message_type": message_type,
        "sender_id": str(AGENT_ID),
        "tenant_id": TENANT_ID,
        "payload": payload or {}
    }
    if ephemeral_interface_id:
        envelope["ephemeral_interface_id"] = ephemeral_interface_id
    if schema_id:
        envelope["schema_id"] = schema_id
    if correlation_id:
        envelope["correlation_id"] = correlation_id
    if causation_id:
        envelope["causation_id"] = causation_id
    return json.dumps(envelope)


async def heartbeat_loop(ws):
    """Send periodic heartbeats."""
    while True:
        await asyncio.sleep(10)
        try:
            heartbeat_msg = create_envelope("agent.heartbeat")
            await ws.send(heartbeat_msg)
        except Exception:
            break


def summarize_text(text: str, max_sentences: int = 2) -> dict:
    """
    Mock text summarization.
    In production, this would use an actual NLP/LLM model.
    """
    # Simple mock: take first N sentences or truncate
    sentences = text.replace("!", ".").replace("?", ".").split(".")
    sentences = [s.strip() for s in sentences if s.strip()]
    
    if len(sentences) <= max_sentences:
        summary = ". ".join(sentences) + "."
    else:
        summary = ". ".join(sentences[:max_sentences]) + "."
    
    # Calculate a mock compression ratio
    compression_ratio = len(summary) / len(text) if text else 0
    
    return {
        "summary": summary,
        "original_length": len(text),
        "summary_length": len(summary),
        "compression_ratio": round(compression_ratio, 2),
        "sentence_count": min(len(sentences), max_sentences),
        "confidence": 0.92
    }


async def main():
    print("=" * 70)
    print(f"📝 SUMMARIZER AGENT STARTING")
    print("=" * 70)
    print(f"Agent ID: {AGENT_ID}")
    print(f"Tenant ID: {TENANT_ID}")
    print(f"Bus URL: {BUS_URL}")
    print("=" * 70)

    try:
        async with websockets.connect(BUS_URL) as ws:
            # Step 1: Register with summarize capability
            print("\n" + "─" * 70)
            print("📝 STEP 1: AGENT REGISTRATION")
            print("─" * 70)
            print("Registering with the semantic bus...")

            # Define schemas for summarization capability
            schemas = {
                "summarize": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string"},
                        "original_length": {"type": "integer"},
                        "summary_length": {"type": "integer"},
                        "compression_ratio": {"type": "number"},
                        "sentence_count": {"type": "integer"},
                        "confidence": {"type": "number"}
                    },
                    "required": ["summary", "original_length", "summary_length"]
                }
            }

            print("\n📋 Capabilities: summarize")
            print("🏷️  Tags: nlp, text-processing")
            print("📊 Schema:")
            print("   • summarize: summary, lengths, compression ratio, confidence")

            register_msg = create_envelope("agent.register", {
                "capabilities": ["summarize"],
                "tags": ["nlp", "text-processing"],
                "schemas": schemas
            })
            await ws.send(register_msg)

            response = await ws.recv()
            data = json.loads(response)
            print(f"\n✅ Registration successful!")
            print(f"   Response: {data['payload']}")

            # Start heartbeat task
            heartbeat_task = asyncio.create_task(heartbeat_loop(ws))

            # Track session state
            active_session_id = None
            chosen_schema_id = None

            print("\n" + "─" * 70)
            print("⏳ WAITING FOR SUMMARIZATION REQUESTS")
            print("─" * 70)
            print("📝 Summarization features:")
            print("   • Extracts key sentences")
            print("   • Configurable summary length")
            print("   • Returns compression metrics")
            print("\n💡 This agent handles TEXT SUMMARIZATION!")
            print("   It can work alongside translator agents for orchestration.")
            print("\n⌨️  Press Ctrl+C to quit")
            print("─" * 70 + "\n")

            try:
                while True:
                    response = await ws.recv()
                    data = json.loads(response)
                    msg_type = data.get("message_type", "unknown")

                    # Step 2: Receive intent.deliver
                    if msg_type == "intent.deliver":
                        print("\n" + "=" * 70)
                        print("🎯 STEP 2: INTENT RECEIVED!")
                        print("=" * 70)
                        intent_data = data['payload'].get('intent', {})
                        print(f"📨 Session ID: {data.get('ephemeral_interface_id')}")
                        print(f"👤 From Agent: {data['payload'].get('original_sender_id')}")
                        print(f"🎪 Capability Requested: {data['payload'].get('capability', 'N/A')}")
                        print(f"\n📋 Intent Details:")
                        if isinstance(intent_data, dict):
                            for key, value in intent_data.items():
                                print(f"   • {key}: {value}")
                        else:
                            print(f"   • intent: {intent_data}")

                        session_id = data.get("ephemeral_interface_id")

                        # Step 3: Send intent.offer
                        print(f"\n" + "─" * 70)
                        print("📤 STEP 3: SENDING OFFER")
                        print("─" * 70)
                        print("🎁 Offer details:")
                        print("   • Schema: schema://summarize.result.v1")
                        print("   • View: view://text.summary.v1")
                        print("   • Required params: text")
                        print("   • Optional: max_sentences")
                        print("   • Score: 0.92")
                        print("   • Engine: mock-summarizer")

                        offer_msg = create_envelope(
                            "intent.offer",
                            {
                                "schema_ids": ["schema://summarize.result.v1"],
                                "view_ids": ["view://text.summary.v1"],
                                "requires": ["text"],
                                "accepts": ["text/plain", "application/json"],
                                "score": 0.92,
                                "metadata": {
                                    "engine": "mock-summarizer",
                                    "max_input_length": 10000,
                                    "default_summary_sentences": 2
                                }
                            },
                            ephemeral_interface_id=session_id
                        )
                        await ws.send(offer_msg)
                        print("✅ Offer sent to bus!")

                    # Receive offer acknowledgment
                    elif msg_type == "intent.offer.received":
                        print(f"\n✅ Offer acknowledged by bus")
                        print(f"   Offer ID: {data['payload']['offer_id']}")

                    # Step 4: Receive session.created
                    elif msg_type == "session.created":
                        print(f"\n" + "=" * 70)
                        print("🤝 STEP 4: SESSION CREATED!")
                        print("=" * 70)
                        print(f"🔗 Session ID: {data['ephemeral_interface_id']}")
                        print(f"👤 Requester ID: {data['payload']['requester_id']}")
                        print(f"📊 Schema: {data['payload']['chosen_schema_id']}")
                        print("✅ Connection established with requester!")

                        active_session_id = data['ephemeral_interface_id']
                        chosen_schema_id = data['payload']['chosen_schema_id']
                        print("\n⏳ Waiting for summarization request...")

                    # Step 5: Receive exec.request
                    elif msg_type == "exec.request":
                        print(f"\n" + "=" * 70)
                        print("📥 STEP 5: EXECUTION REQUEST RECEIVED!")
                        print("=" * 70)

                        # IMPORTANT: Use session ID from THIS request for orchestrated flows
                        request_session_id = data.get('ephemeral_interface_id') or active_session_id
                        request_schema_id = data.get('schema_id') or chosen_schema_id or "schema://summarize.result.v1"
                        
                        text = data['payload'].get('text', '')
                        max_sentences = data['payload'].get('max_sentences', 2)
                        exec_id = data['payload'].get('exec_id')
                        
                        # Check if this is an orchestrated flow step
                        is_orchestrated = data['payload'].get('orchestration_step') is not None
                        if is_orchestrated:
                            step = data['payload'].get('orchestration_step', 1)
                            total = data['payload'].get('orchestration_total', 1)
                            previous_results = data['payload'].get('previous_results', {})
                            print(f"🔗 ORCHESTRATED FLOW: Step {step}/{total}")
                            
                            # In orchestrated flows, we might get input from previous agent
                            # Check if there's translated text from translator
                            if previous_results:
                                print(f"   Previous results available from: {list(previous_results.keys())}")
                                # Use translated text if available
                                for agent_id, result in previous_results.items():
                                    if isinstance(result, dict) and 'translated_text' in result:
                                        text = result['translated_text']
                                        print(f"   Using translated text: '{text}'")
                                        break

                        print(f"📝 Summarization requested:")
                        print(f"   • Text length: {len(text)} chars")
                        print(f"   • Max sentences: {max_sentences}")
                        print(f"   • Exec ID: {exec_id}")
                        print(f"   • Request Session ID: {request_session_id}")
                        print(f"\n📄 Original text preview:")
                        preview = text[:100] + "..." if len(text) > 100 else text
                        print(f"   '{preview}'")

                        # Step 6: Send exec.result
                        print(f"\n" + "─" * 70)
                        print("📝 STEP 6: PERFORMING SUMMARIZATION")
                        print("─" * 70)

                        # Perform summarization
                        result = summarize_text(text, max_sentences)

                        print(f"✅ Summarization complete!")
                        print(f"   📝 Summary: '{result['summary']}'")
                        print(f"   📊 Compression: {result['compression_ratio']*100:.0f}%")
                        print(f"   🎯 Confidence: {result['confidence']*100:.0f}%")

                        print(f"\n📤 Sending result back (session: {request_session_id})...")

                        result_msg = create_envelope(
                            "exec.result",
                            {
                                "exec_id": exec_id,
                                "result": result
                            },
                            ephemeral_interface_id=request_session_id,
                            schema_id=request_schema_id
                        )
                        await ws.send(result_msg)
                        print("✅ Result sent!")
                        print("\n⏳ Waiting for next request...")

                    # Handle session close
                    elif msg_type == "session.closed":
                        print(f"\n🔚 Session closed: {data.get('ephemeral_interface_id')}")
                        print(f"   Reason: {data['payload'].get('reason', 'unknown')}")
                        active_session_id = None
                        chosen_schema_id = None
                        print("\n⏳ Waiting for new requests...")

                    # Handle errors
                    elif msg_type == "error":
                        print(f"\n❌ Error: {data['payload']}")

                    # Log other messages
                    else:
                        print(f"   📨 Received: {msg_type}")

            except asyncio.CancelledError:
                pass
            finally:
                heartbeat_task.cancel()

    except ConnectionRefusedError:
        print("\n" + "=" * 70)
        print("❌ CONNECTION ERROR")
        print("=" * 70)
        print("Cannot connect to the semantic bus!")
        print("💡 Start the server with:")
        print("   cd semantic-bus")
        print("   uvicorn src.transport.app:app --reload")
        print("=" * 70)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\n" + "=" * 70)
        print("👋 SUMMARIZER AGENT SHUTTING DOWN")
        print("=" * 70)
    except Exception as e:
        print("\n" + "=" * 70)
        print("❌ UNEXPECTED ERROR")
        print("=" * 70)
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        print("=" * 70)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
