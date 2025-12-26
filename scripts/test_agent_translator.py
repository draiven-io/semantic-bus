#!/usr/bin/env python3
"""
Test Agent 2 - Provider (Full Negotiation Flow)

This agent demonstrates a provider in the LIP negotiation lifecycle:
1. Registers with the bus with "translate" capability
2. Waits for intent.deliver messages
3. Sends intent.offer with supported schemas
4. Receives session.created when requester agrees
5. Receives exec.request
6. Sends exec.result

Usage:
    python scripts/test_agent2.py

Run this BEFORE test_agent1.py to be available when the intent is broadcast.
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

MOCK_RESULT = """
A inteligência artificial revolucionou muitos setores nos últimos anos.

Do diagnóstico na área da saúde aos veículos autônomos, os sistemas de IA estão se tornando
cada vez mais sofisticados. Algoritmos de aprendizado de máquina agora podem analisar vastas
quantidades de dados para identificar padrões que seriam impossíveis de serem detectados por humanos.

O processamento de linguagem natural permitiu que as máquinas entendessem e gerassem
a linguagem humana com notável precisão. O futuro promete ainda mais
avanços à medida que a pesquisa continua a progredir.
"""

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


async def main():
    print("="*70)
    print(f"🌐 TRANSLATOR AGENT STARTING")
    print("="*70)
    print(f"Agent ID: {AGENT_ID}")
    print(f"Tenant ID: {TENANT_ID}")
    print(f"Bus URL: {BUS_URL}")
    print("="*70)
    
    try:
        async with websockets.connect(BUS_URL) as ws:
            # Step 1: Register with translate capability
            print("\n" + "─"*70)
            print("📝 STEP 1: AGENT REGISTRATION")
            print("─"*70)
            print("Registering with the semantic bus...")
            
            # Define schemas for each capability this agent provides
            # Key = capability name, Value = JSON Schema for that capability's response
            schemas = {
                "translate": {
                    "type": "object",
                    "properties": {
                        "translated_text": {"type": "string"},
                        "source_language": {"type": "string"},
                        "target_language": {"type": "string"},
                        "confidence": {"type": "number"},
                        "original_text": {"type": "string"}
                    },
                    "required": ["translated_text", "source_language", "target_language"]
                },
                "nlp": {
                    "type": "object",
                    "properties": {
                        "analysis": {"type": "object"},
                        "confidence": {"type": "number"}
                    },
                    "required": ["analysis"]
                }
            }
            
            print("\n📋 Capabilities: translate, nlp")
            print("🏷️  Tags: fast, premium")
            print("📊 Schemas:")
            print("   • translate: translated_text, source/target language, confidence")
            print("   • nlp: text analysis with confidence")
            
            register_msg = create_envelope("agent.register", {
                "capabilities": ["translate", "nlp"],
                "tags": ["fast", "premium"],
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
            
            print("\n" + "─"*70)
            print("⏳ WAITING FOR TRANSLATION REQUESTS")
            print("─"*70)
            print("🌐 Supported languages:")
            print("   • English (en)")
            print("   • Portuguese (pt)")
            print("   • Spanish (es)")
            print("   • French (fr)")
            print("\n💡 This agent is DIFFERENT from calculator agents!")
            print("   It handles language translation, not math operations.")
            print("\n⌨️  Press Ctrl+C to quit")
            print("─"*70 + "\n")
            
            try:
                while True:
                    response = await ws.recv()
                    data = json.loads(response)
                    msg_type = data.get("message_type", "unknown")
                    
                    # Step 2: Receive intent.deliver
                    if msg_type == "intent.deliver":
                        print("\n" + "="*70)
                        print("🎯 STEP 2: INTENT RECEIVED!")
                        print("="*70)
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
                        print(f"\n" + "─"*70)
                        print("📤 STEP 3: SENDING OFFER")
                        print("─"*70)
                        print("🎁 Offer details:")
                        print("   • Schema: schema://translate.result.v1")
                        print("   • View: view://text.simple.v1")
                        print("   • Required params: text, source_language, target_language")
                        print("   • Score: 0.95 (high confidence!)")
                        print("   • Engine: mock-translator")
                        print("   • Languages: en, pt, es, fr")
                        
                        offer_msg = create_envelope(
                            "intent.offer",
                            {
                                "schema_ids": ["schema://translate.result.v1"],
                                "view_ids": ["view://text.simple.v1"],
                                "requires": ["text", "source_language", "target_language"],
                                "accepts": ["text/plain", "application/json"],
                                "score": 0.95,
                                "metadata": {
                                    "engine": "mock-translator",
                                    "languages": ["en", "pt", "es", "fr"]
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
                        print(f"\n" + "="*70)
                        print("🤝 STEP 4: SESSION CREATED!")
                        print("="*70)
                        print(f"🔗 Session ID: {data['ephemeral_interface_id']}")
                        print(f"👤 Requester ID: {data['payload']['requester_id']}")
                        print(f"📊 Schema: {data['payload']['chosen_schema_id']}")
                        print("✅ Connection established with requester!")
                        
                        active_session_id = data['ephemeral_interface_id']
                        chosen_schema_id = data['payload']['chosen_schema_id']
                        print("\n⏳ Waiting for translation request...")
                    
                    # Step 5: Receive exec.request
                    elif msg_type == "exec.request":
                        print(f"\n" + "="*70)
                        print("📥 STEP 5: EXECUTION REQUEST RECEIVED!")
                        print("="*70)
                        
                        # IMPORTANT: Use the session ID from THIS request, not the saved one
                        # This is critical for orchestrated flows where the bus sends
                        # exec.request directly without session.created
                        request_session_id = data.get('ephemeral_interface_id') or active_session_id
                        request_schema_id = data.get('schema_id') or chosen_schema_id
                        
                        original_text = data['payload'].get('text', '')
                        source_lang = data['payload'].get('source_language', 'en')
                        target_lang = data['payload'].get('target_language', 'pt')
                        
                        # Check if this is an orchestrated flow step
                        is_orchestrated = data['payload'].get('orchestration_step') is not None
                        if is_orchestrated:
                            step = data['payload'].get('orchestration_step', 1)
                            total = data['payload'].get('orchestration_total', 1)
                            print(f"🔗 ORCHESTRATED FLOW: Step {step}/{total}")
                        
                        print(f"🌐 Translation requested:")
                        print(f"   • Original text: '{original_text}'")
                        print(f"   • Source language: {source_lang}")
                        print(f"   • Target language: {target_lang}")
                        print(f"   • Exec ID: {data['payload'].get('exec_id')}")
                        print(f"   • Request Session ID: {request_session_id}")
                        
                        # Step 6: Send exec.result
                        print(f"\n" + "─"*70)
                        print("🌐 STEP 6: PERFORMING TRANSLATION")
                        print("─"*70)
                                                
                        print(f"✅ Translation successful!")
                        print(f"   📝 Original: '{original_text}'")
                        print(f"   ✨ Translated: '{MOCK_RESULT}'")
                        print(f"   🎯 Confidence: 98%")
                        
                        print(f"\n📤 Sending result back (session: {request_session_id})...")
                        
                        # Build result matching the "translate" capability schema
                        # Use the session ID from the exec.request, not the saved active_session_id
                        result_msg = create_envelope(
                            "exec.result",
                            {
                                "result": {
                                    "translated_text": MOCK_RESULT,
                                    "source_language": "en",
                                    "target_language": "pt",
                                    "confidence": 0.98,
                                    "original_text": original_text
                                },
                                "exec_id": data['payload'].get('exec_id')
                            },
                            ephemeral_interface_id=request_session_id,
                            schema_id=request_schema_id,
                            correlation_id=data.get("correlation_id"),
                            causation_id=data.get("message_id")
                        )
                        await ws.send(result_msg)
                        print(f"✅ Result sent successfully!")
                    
                    # Receive session.close
                    elif msg_type == "session.close":
                        print(f"\n" + "="*70)
                        print("🔚 SESSION CLOSED")
                        print("="*70)
                        print(f"📝 Reason: {data['payload'].get('reason')}")
                        print(f"👤 Closed by: {data['payload'].get('closed_by')}")
                        active_session_id = None
                        chosen_schema_id = None
                        print("\n⏳ Ready for new translation requests...")
                        print("─"*70 + "\n")
                    
                    # Ignore heartbeat acks
                    elif msg_type == "agent.heartbeat_ack":
                        pass
                    
                    # Log other messages
                    else:
                        print(f"\n   📨 Received {msg_type}")
            
            except asyncio.CancelledError:
                pass
            finally:
                heartbeat_task.cancel()
            
    except ConnectionRefusedError:
        print("\n" + "="*70)
        print("❌ CONNECTION ERROR")
        print("="*70)
        print("Cannot connect to the semantic bus!")
        print("💡 Start the server with:")
        print("   cd semantic-bus")
        print("   uvicorn src.transport.app:app --reload")
        print("="*70)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\n" + "="*70)
        print("👋 TRANSLATOR AGENT SHUTTING DOWN")
        print("="*70)
    except Exception as e:
        print("\n" + "="*70)
        print("❌ UNEXPECTED ERROR")
        print("="*70)
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        print("="*70)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
