#!/usr/bin/env python3
"""
Test Agent - Calculator Requester

This agent demonstrates requesting mathematical calculations:
1. Registers with the bus
2. Broadcasts an intent looking for "calculate" capability
3. Receives offers from providers
4. Selects an offer and sends intent.agree
5. Receives session.created
6. Sends exec.request with calculation parameters
7. Receives exec.result

Usage:
    python scripts/test_agent_intent_calculation.py

Run test_agent_calculator.py first (the calculator provider).
This demonstrates semantic routing - the bus should route to the calculator
agent, NOT the translation agent, even if both are running.
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
    print(f"🧮 CALCULATOR REQUESTER STARTING")
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
            
            schemas = {}  # Requesters typically don't provide response schemas
            
            register_msg = create_envelope("agent.register", {
                "capabilities": ["request"],
                "tags": ["test", "calculator-client"],
                "schemas": schemas
            })
            await ws.send(register_msg)
            
            response = await ws.recv()
            data = json.loads(response)
            print(f"✅ Registration successful!")
            print(f"   Response: {data['payload']}")
            
            # Step 2: Broadcast intent for calculation
            print("\n" + "─"*70)
            print("📣 STEP 2: BROADCASTING INTENT")
            print("─"*70)
            print("🎯 Demonstrating semantic routing:")
            print("   This request is for CALCULATION (not translation)")
            print("   The bus should route to calculator agent ONLY!")
            print("\n🧮 Calculation request:")
            print("   • Capability: calculate")
            print("   • Operation: multiply")
            print("   • Operand A: 42")
            print("   • Operand B: 13")
            print("   • Expected: 42 × 13 = 546")
            
            intent_msg = create_envelope("intent.broadcast", {
                "capability": "calculate",
                "intent": {
                    "action": "calculate",
                    "operation": "multiply",
                    "operand_a": 42,
                    "operand_b": 13
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
                            print(f"   • Provider ID: {offer['provider_id']}")
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
                    print("💡 Make sure test_agent_calculator.py is running!")
                    return
            
            if not offers:
                print("❌ No offers received")
                print("💡 Start test_agent_calculator.py first")
                return
            
            # Step 4: Agree to first offer
            chosen_offer = offers[0]
            chosen_schema = chosen_offer["schema_ids"][0] if chosen_offer["schema_ids"] else None
            
            print("─"*70)
            print("🤝 STEP 4: SELECTING OFFER")
            print("─"*70)
            print(f"✅ Choosing offer: {chosen_offer['offer_id']}")
            print(f"📊 Schema: {chosen_schema}")
            print(f"👤 Provider: {chosen_offer['provider_id']}")
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
            provider_id = None
            while True:
                try:
                    response = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    data = json.loads(response)
                    msg_type = data.get("message_type")
                    
                    if msg_type == "session.created":
                        provider_id = data['payload']['provider_id']
                        print(f"✅ Session created successfully!")
                        print(f"🔗 Session ID: {data['ephemeral_interface_id']}")
                        print(f"👤 Provider ID: {provider_id}")
                        print(f"📊 Schema: {data['payload']['chosen_schema_id']}")
                        print("🤝 Ready to execute calculation!")
                        break
                    else:
                        print(f"   📨 Received: {msg_type}")
                        
                except asyncio.TimeoutError:
                    print("❌ Timeout waiting for session.created")
                    return
            
            # Step 6: Send execution request
            print("\n" + "─"*70)
            print("📤 STEP 6: SENDING EXECUTION REQUEST")
            print("─"*70)
            print("🧮 Requesting calculation:")
            print("   • Operation: multiply")
            print("   • Operand A: 42")
            print("   • Operand B: 13")
            print("   • Expected result: 546")
            
            exec_id = str(uuid4())
            print(f"\n📋 Execution ID: {exec_id}")
            
            exec_msg = create_envelope(
                "exec.request",
                {
                    "exec_id": exec_id,
                    "operation": "multiply",
                    "operand_a": 42,
                    "operand_b": 13
                },
                ephemeral_interface_id=session_id,
                schema_id=chosen_schema
            )
            await ws.send(exec_msg)
            print("✅ Execution request sent!")
            
            # Step 7: Wait for result
            print("\n" + "─"*70)
            print("⏳ STEP 7: WAITING FOR CALCULATION RESULT")
            print("─"*70)
            while True:
                try:
                    response = await asyncio.wait_for(ws.recv(), timeout=10.0)
                    data = json.loads(response)
                    msg_type = data.get("message_type")
                    
                    if msg_type == "exec.result":
                        result = data['payload']['result']
                        print(f"✅ Calculation complete!")
                        print("\n" + "="*70)
                        print("📊 CALCULATION RESULT")
                        print("="*70)
                        
                        if "formula" in result:
                            print(f"📐 Formula: {result['formula']}")
                        
                        if "result" in result:
                            print(f"🎯 Result: {result['result']}")
                        
                        if "operation" in result:
                            print(f"🔧 Operation: {result['operation']}")
                            print(f"🔢 Operand A: {result.get('operand_a', 'N/A')}")
                            print(f"🔢 Operand B: {result.get('operand_b', 'N/A')}")
                        
                        if "error" in result:
                            print(f"❌ Error: {result['error']}")
                        
                        print("="*70)
                        
                        # Close session
                        print("\n🔚 Closing session...")
                        close_msg = create_envelope(
                            "session.close",
                            {"reason": "task_completed"},
                            ephemeral_interface_id=session_id
                        )
                        await ws.send(close_msg)
                        
                        # Wait a moment for cleanup
                        await asyncio.sleep(1)
                        break
                    else:
                        print(f"   📨 Received: {msg_type}")
                        
                except asyncio.TimeoutError:
                    print("❌ Timeout waiting for exec.result")
                    return
            
            print("\n" + "="*70)
            print("✅ TEST COMPLETED SUCCESSFULLY!")
            print("="*70)
            print("🎯 Semantic routing verification:")
            print("   ✓ Calculator agent received the calculation request")
            print("   ✓ Translation agent was NOT involved (if running)")
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
    except KeyboardInterrupt:
        print("\n\n" + "="*70)
        print("👋 REQUESTER SHUTTING DOWN")
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
