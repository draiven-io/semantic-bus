#!/usr/bin/env python3
"""
Test Agent - Requester for Multi-Agent Orchestration

This agent demonstrates multi-agent orchestration by requesting a compound task:
1. Translate some text from English to Portuguese
2. Then summarize the translated text

This tests the AgentFlowPlanner which uses LLM + LangGraph to:
1. Discover that both translator and summarizer agents are needed
2. Build a flow: translator → summarizer (sequential dependency)
3. Execute the flow and aggregate results

Usage:
    1. Start the semantic bus server
    2. Start test_agent_translator.py
    3. Start test_agent_summarizer.py
    4. Run this script: python scripts/test_agent_intent_translate_and_summarize.py
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

# Sample text to translate and summarize
SAMPLE_TEXT = """
Artificial intelligence has revolutionized many industries in recent years. 
From healthcare diagnostics to autonomous vehicles, AI systems are becoming 
increasingly sophisticated. Machine learning algorithms can now analyze vast 
amounts of data to identify patterns that would be impossible for humans to detect.
Natural language processing has enabled machines to understand and generate 
human language with remarkable accuracy. The future promises even more 
breakthroughs as research continues to advance.
"""


def create_envelope(
    message_type: str,
    payload: dict = None,
    ephemeral_interface_id: str = None,
    schema_id: str = None,
    correlation_id: str = None,
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
    return json.dumps(envelope)


async def main():
    print("=" * 70)
    print("🚀 MULTI-AGENT ORCHESTRATION TEST")
    print("   Translate + Summarize Pipeline")
    print("=" * 70)
    print(f"Agent ID: {AGENT_ID}")
    print(f"Tenant ID: {TENANT_ID}")
    print(f"Bus URL: {BUS_URL}")
    print("=" * 70)
    
    try:
        async with websockets.connect(BUS_URL) as ws:
            # Step 1: Register
            print("\n" + "─" * 70)
            print("📝 STEP 1: AGENT REGISTRATION")
            print("─" * 70)
            
            register_msg = create_envelope("agent.register", {
                "capabilities": ["request"],
                "tags": ["test", "orchestration-client"],
                "schemas": {}
            })
            await ws.send(register_msg)
            
            response = await ws.recv()
            data = json.loads(response)
            print(f"✅ Registration successful!")
            print(f"   Response: {data['payload']}")
            
            # Step 2: Broadcast a COMPOUND intent (translate + summarize)
            print("\n" + "─" * 70)
            print("📣 STEP 2: BROADCASTING COMPOUND INTENT")
            print("─" * 70)
            print("🎯 This is a MULTI-AGENT request:")
            print("   1️⃣  First: Translate text from English to Portuguese")
            print("   2️⃣  Then: Summarize the translated text")
            print("\n🔄 The bus should orchestrate:")
            print("   • Discover both translator and summarizer agents")
            print("   • Build a flow: translator → summarizer")
            print("   • Execute sequentially (summarizer depends on translator)")
            print("\n📝 Original text (English):")
            for line in SAMPLE_TEXT.strip().split('\n'):
                if line.strip():
                    print(f"   {line.strip()}")
            
            # The intent describes what we want to accomplish
            # The bus/flow planner will figure out the agents needed
            intent_msg = create_envelope("intent.broadcast", {
                # Natural language intent - LLM will parse this
                "intent": "Translate this text to Portuguese and then summarize it",
                # Explicit parameters for the task
                "params": {
                    "text": SAMPLE_TEXT.strip(),
                    "source_language": "en",
                    "target_language": "pt",
                    "summary_length": "short"
                },
                # Allow multi-agent orchestration
                "allow_delegation": True,
            })
            await ws.send(intent_msg)
            print("\n📤 Compound intent broadcast sent!")
            print("⏳ Waiting for orchestration response...")
            
            # Step 3: Wait for response(s)
            # With multi-agent orchestration, we might get:
            # - intent.offers with multiple offers
            # - exec.result with aggregated results
            # - Or flow execution updates
            
            print("\n" + "─" * 70)
            print("⏳ STEP 3: WAITING FOR ORCHESTRATION RESPONSE")
            print("─" * 70)
            
            session_id = None
            offers = []
            results = {}
            timeout_count = 0
            max_timeouts = 6  # 60 seconds total
            agreed = False  # Track if we've already agreed to an offer
            
            while timeout_count < max_timeouts:
                try:
                    response = await asyncio.wait_for(ws.recv(), timeout=10.0)
                    data = json.loads(response)
                    msg_type = data.get("message_type")
                    
                    print(f"\n📨 Received: {msg_type}")
                    
                    if msg_type == "intent.offers":
                        session_id = data.get("ephemeral_interface_id")
                        offers = data["payload"].get("offers", [])
                        print(f"✅ Received {len(offers)} offer(s)")
                        print(f"🔗 Session ID: {session_id}")
                        
                        for i, offer in enumerate(offers, 1):
                            print(f"\n   📦 Offer #{i}:")
                            print(f"      • Offer ID: {offer.get('offer_id', 'N/A')}")
                            print(f"      • Provider: {offer.get('provider_id', 'N/A')}")
                            print(f"      • Schemas: {offer.get('schema_ids', [])}")
                            print(f"      • Score: {offer.get('score', 'N/A')}")
                            metadata = offer.get('metadata', {})
                            if metadata.get('is_orchestrated_flow'):
                                print(f"      • 🔄 ORCHESTRATED FLOW!")
                                print(f"      • Flow Agents: {metadata.get('flow_agents', [])}")
                                print(f"      • Flow Reasoning: {metadata.get('flow_reasoning', 'N/A')[:50]}...")
                        
                        # Only agree to orchestrated offers (from the bus)
                        # Look for an offer with is_orchestrated_flow metadata
                        if not agreed and offers:
                            orchestrated_offer = None
                            for offer in offers:
                                metadata = offer.get('metadata', {})
                                if metadata.get('is_orchestrated_flow'):
                                    orchestrated_offer = offer
                                    break
                            
                            if orchestrated_offer:
                                chosen_schema = orchestrated_offer.get("schema_ids", [None])[0]
                                print(f"\n🤝 Agreeing to ORCHESTRATED offer: {orchestrated_offer.get('offer_id')}")
                                agree_msg = create_envelope(
                                    "intent.agree",
                                    {
                                        "offer_id": orchestrated_offer["offer_id"],
                                        "chosen_schema_id": chosen_schema
                                    },
                                    ephemeral_interface_id=session_id
                                )
                                await ws.send(agree_msg)
                                agreed = True
                            else:
                                print("   ⏳ Waiting for orchestrated offer...")
                    
                    elif msg_type == "session.created":
                        print(f"✅ Session created!")
                        print(f"   Schema: {data['payload'].get('chosen_schema_id')}")
                        
                        # Send execution request
                        print("\n📤 Sending execution request...")
                        exec_msg = create_envelope(
                            "exec.request",
                            {
                                "text": SAMPLE_TEXT.strip(),
                                "source_language": "en",
                                "target_language": "pt",
                                "summary_length": "short"
                            },
                            ephemeral_interface_id=session_id
                        )
                        await ws.send(exec_msg)
                    
                    elif msg_type == "exec.result":
                        payload = data.get("payload", {})
                        result = payload.get("result", {})
                        capability = payload.get("_capability", "unknown")
                        is_aggregated = payload.get("aggregated", False)
                        
                        print(f"\n✅ Execution result received!")
                        print(f"   Capability: {capability}")
                        print(f"   Aggregated: {is_aggregated}")
                        
                        # Store result
                        results[capability] = result
                        
                        # Check if this is the final aggregated orchestrated result
                        if is_aggregated or capability == "orchestrated_flow":
                            print("\n" + "=" * 70)
                            print("🎉 MULTI-AGENT ORCHESTRATION COMPLETE!")
                            print("=" * 70)
                            
                            # Show flow metadata if available
                            flow_meta = payload.get("flow_metadata", {})
                            if flow_meta:
                                print(f"\n📊 Flow Stats:")
                                print(f"   Steps: {flow_meta.get('steps', 'N/A')}")
                                print(f"   Success: {flow_meta.get('success', 'N/A')}")
                            
                            # Show translation result if present
                            if "translated_text" in result:
                                print("\n📝 Translation Result:")
                                translated = result['translated_text']
                                if len(translated) > 200:
                                    print(f"   {translated[:200]}...")
                                else:
                                    print(f"   {translated}")
                            
                            # Show summary result if present
                            if "summary" in result:
                                print("\n📋 Summary Result:")
                                print(f"   {result['summary']}")
                            
                            print("\n" + "=" * 70)
                            break
                        
                        # Single capability result - might be part of flow
                        print(f"   Result: {json.dumps(result, indent=2)[:500]}")
                        
                        # If we got translation, we might be waiting for summarization
                        if capability == "translate" and "translated_text" in result:
                            print("\n⏳ Translation complete, waiting for summarization...")
                    
                    elif msg_type == "flow.update":
                        # Flow execution updates
                        flow_data = data.get("payload", {})
                        step = flow_data.get("step", "unknown")
                        agent = flow_data.get("agent_id", "unknown")
                        status = flow_data.get("status", "unknown")
                        print(f"   🔄 Flow step {step}: {agent} - {status}")
                    
                    elif msg_type == "error":
                        error_payload = data.get("payload", {})
                        print(f"\n❌ Error received:")
                        print(f"   Code: {error_payload.get('error_code', 'unknown')}")
                        print(f"   Message: {error_payload.get('error', 'unknown')}")
                        break
                    
                    else:
                        print(f"   Payload: {json.dumps(data.get('payload', {}), indent=2)[:200]}")
                    
                    timeout_count = 0  # Reset on successful receive
                    
                except asyncio.TimeoutError:
                    timeout_count += 1
                    print(f"⏳ Waiting... ({timeout_count * 10}s)")
            
            if timeout_count >= max_timeouts:
                print("\n❌ Timeout waiting for complete response")
                print("💡 Check that both agents are running:")
                print("   • test_agent_translator.py")
                print("   • test_agent_summarizer.py")
            
            # Print collected results
            if results:
                print("\n" + "=" * 70)
                print("📊 COLLECTED RESULTS")
                print("=" * 70)
                print(json.dumps(results, indent=2)[:1000])
            
            # Close session if we have one
            if session_id:
                print(f"\n🔚 Closing session...")
                close_msg = create_envelope(
                    "session.close",
                    {"reason": "completed"},
                    ephemeral_interface_id=session_id
                )
                await ws.send(close_msg)
                print("✅ Session closed")
            
            print("\n" + "=" * 70)
            print("🏁 TEST COMPLETED")
            print("=" * 70)
            
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
