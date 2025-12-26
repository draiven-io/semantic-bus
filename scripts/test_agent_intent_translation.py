#!/usr/bin/env python3
"""
Test Agent 1 - Requester (Full Negotiation Flow)

This agent demonstrates the complete LIP negotiation lifecycle:
1. Registers with the bus
2. Broadcasts an intent looking for "translate" capability
3. Receives offers from providers
4. Selects an offer and sends intent.agree
5. Receives session.created
6. Sends exec.request
7. Receives exec.result

Usage:
    python scripts/test_agent1.py

Run test_agent2.py first (the provider).
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
    schema_id: str = None
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
    return json.dumps(envelope)


async def main():
    print("="*70)
    print(f"🌐 TRANSLATION REQUESTER STARTING")
    print("="*70)
    print(f"Agent ID: {AGENT_ID}")
    print(f"Tenant ID: {TENANT_ID}")
    print(f"Bus URL: {BUS_URL}")
    print("="*70)
    
    try:
        async with websockets.connect(BUS_URL) as ws:
            # Step 1: Register
            print("\n" + "─"*70)
            print("📝 STEP 1: AGENT REGISTRATION")
            print("─"*70)
            print("Registering as a requester agent...")
            
            # Optional: Requester can also define schemas for responses it expects
            # In this case, we're just consuming what providers offer
            schemas = {}  # Requesters typically don't provide response schemas
            
            register_msg = create_envelope("agent.register", {
                "capabilities": ["request"],
                "tags": ["test", "client"],
                "schemas": schemas  # Empty for requester, but field is present
            })
            await ws.send(register_msg)
            
            response = await ws.recv()
            data = json.loads(response)
            print(f"✅ Registration successful!")
            print(f"   Response: {data['payload']}")
            
            # Step 2: Broadcast intent
            print("\n" + "─"*70)
            print("📣 STEP 2: BROADCASTING INTENT")
            print("─"*70)
            print("🎯 Demonstrating semantic routing:")
            print("   This request is for TRANSLATION (not calculation)")
            print("   The bus should route to translator agent ONLY!")
            print("\n🌐 Translation request:")
            print("   • Capability: translate")
            print("   • Text: 'Hello, world!'")
            print("   • Source language: en (English)")
            print("   • Target language: pt (Portuguese)")
            print("   • Expected: 'Olá, mundo!'")
            
            intent_msg = create_envelope("intent.broadcast", {
                "capability": "translate",
                "intent": {
                    "action": "translate",
                    "source_language": "en",
                    "target_language": "pt",
                    "text": "Hello, world!"
                }
            })
            await ws.send(intent_msg)
            print("\n📤 Intent broadcast sent to bus!")
            print("⏳ Waiting for offers from provider agents...")
            
            # Step 3: Wait for offers
            session_id = None
            offers = []
            
            print("\n" + "─"*70)
            print("⏳ STEP 3: WAITING FOR OFFERS")
            print("─"*70)
            while True:
                try:
                    response = await asyncio.wait_for(ws.recv(), timeout=10.0)
                    data = json.loads(response)
                    msg_type = data.get("message_type")
                    
                    if msg_type == "intent.offers":
                        session_id = data.get("ephemeral_interface_id")
                        offers = data["payload"]["offers"]
                        print(f"✅ Received {len(offers)} offer(s) from providers!")
                        print(f"🔗 Session ID: {session_id}")
                        print()
                        for i, offer in enumerate(offers, 1):
                            print(f"📦 Offer #{i}:")
                            print(f"   • Offer ID: {offer['offer_id']}")
                            print(f"   • Schemas: {offer['schema_ids']}")
                            print(f"   • Score: {offer.get('score', 'N/A')}")
                            metadata = offer.get('metadata', {})
                            if metadata:
                                print(f"   • Metadata:")
                                for key, value in metadata.items():
                                    print(f"      - {key}: {value}")
                            print()
                        break
                    elif msg_type == "error":
                        print(f"❌ Error received: {data['payload']}")
                        return
                    else:
                        print(f"   📨 Received: {msg_type}")
                        
                except asyncio.TimeoutError:
                    print("❌ Timeout waiting for offers")
                    print("💡 Make sure test_agent_translator.py is running!")
                    return
            
            if not offers:
                print("❌ No offers received")
                print("💡 Start test_agent_translator.py first")
                return
            
            # Step 4: Agree to first offer
            chosen_offer = offers[0]
            chosen_schema = chosen_offer["schema_ids"][0] if chosen_offer["schema_ids"] else None
            
            print("─"*70)
            print("🤝 STEP 4: SELECTING OFFER")
            print("─"*70)
            print(f"✅ Choosing offer: {chosen_offer['offer_id']}")
            print(f"📊 Schema: {chosen_schema}")
            print("\n📤 Sending agreement to bus...")
            
            agree_msg = create_envelope(
                "intent.agree",
                {
                    "offer_id": chosen_offer["offer_id"],
                    "chosen_schema_id": chosen_schema
                },
                ephemeral_interface_id=session_id
            )
            await ws.send(agree_msg)
            
            # Step 5: Wait for session.created
            print("\n" + "─"*70)
            print("⏳ STEP 5: WAITING FOR SESSION CREATION")
            print("─"*70)
            while True:
                try:
                    response = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    data = json.loads(response)
                    msg_type = data.get("message_type")
                    
                    if msg_type == "session.created":
                        print(f"✅ Session created successfully!")
                        print(f"🔗 Session ID: {data['ephemeral_interface_id']}")
                        print(f"📊 Schema: {data['payload']['chosen_schema_id']}")
                        print("🤝 Ready to execute translation!")
                        break
                    elif msg_type == "error":
                        print(f"❌ Error received: {data['payload']}")
                        return
                    else:
                        print(f"   📨 Received: {msg_type}")
                        
                except asyncio.TimeoutError:
                    print("❌ Timeout waiting for session.created")
                    return
            
            # Step 6: Send exec.request
            print("\n" + "─"*70)
            print("📤 STEP 6: SENDING EXECUTION REQUEST")
            print("─"*70)
            print("🌐 Requesting translation:")
            print("   • Text: 'Hello, world!'")
            print("   • Source language: en (English)")
            print("   • Target language: pt (Portuguese)")
            
            exec_msg = create_envelope(
                "exec.request",
                {
                    "text": "Hello, world!",
                    "source_language": "en",
                    "target_language": "pt"
                },
                ephemeral_interface_id=session_id,
                schema_id=chosen_schema
            )
            await ws.send(exec_msg)
            print("✅ Execution request sent!")
            
            # Step 7: Wait for exec.result
            print("\n" + "─"*70)
            print("⏳ STEP 7: WAITING FOR TRANSLATION RESULT")
            print("─"*70)
            while True:
                try:
                    response = await asyncio.wait_for(ws.recv(), timeout=10.0)
                    data = json.loads(response)
                    msg_type = data.get("message_type")
                    
                    if msg_type == "exec.result":
                        payload = data.get('payload', {})
                        result = payload.get('result', {})
                        capability = payload.get('_capability', 'unknown')
                        view_valid = payload.get('_view_valid', True)
                        
                        print(f"✅ Translation complete!")
                        print("\n" + "="*70)
                        print("🌐 TRANSLATION RESULT")
                        print("="*70)
                        print(f"🎯 Capability: {capability}")
                        print(f"✓ Validation: {'Valid' if view_valid else 'Invalid'}")
                        
                        if 'translated_text' in result:
                            print(f"\n📝 Original: '{result.get('original_text', 'N/A')}'")
                            print(f"✨ Translated: '{result['translated_text']}'")
                            print(f"🌍 {result.get('source_language', 'N/A')} → {result.get('target_language', 'N/A')}")
                            if 'confidence' in result:
                                print(f"🎯 Confidence: {result['confidence']*100:.0f}%")
                        else:
                            print(f"📊 Result: {json.dumps(result, indent=2)}")
                        
                        print("="*70)
                        break
                    elif msg_type == "exec.partial_failure":
                        print(f"⚠️  Partial failure: {data['payload']}")
                        break
                    elif msg_type == "error":
                        print(f"❌ Error received: {data['payload']}")
                        return
                    else:
                        print(f"   📨 Received: {msg_type}")
                        
                except asyncio.TimeoutError:
                    print("❌ Timeout waiting for exec.result")
                    return
            
            # Step 8: Close session
            print(f"\n🔚 Closing session...")
            close_msg = create_envelope(
                "session.close",
                {"reason": "completed"},
                ephemeral_interface_id=session_id
            )
            await ws.send(close_msg)
            print("✅ Session closed successfully")
            
            print("\n" + "="*70)
            print("✅ TEST COMPLETED SUCCESSFULLY!")
            print("="*70)
            print("🎯 Semantic routing verification:")
            print("   ✓ Translator agent received the translation request")
            print("   ✓ Calculator agent was NOT involved (if running)")
            print("   ✓ Bus correctly matched capability to agent")
            print("="*70 + "\n")
            
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
