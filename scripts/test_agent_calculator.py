#!/usr/bin/env python3
"""
Test Agent - Calculator Provider

This agent demonstrates a calculator service provider that:
1. Registers with the bus with "calculate" capability
2. Waits for intent.deliver messages
3. Sends intent.offer with supported schemas
4. Receives session.created when requester agrees
5. Receives exec.request
6. Performs mathematical operations and sends exec.result

Usage:
    python scripts/test_agent_calculator.py

This agent handles mathematical operations (add, subtract, multiply, divide)
completely different from translation services, demonstrating semantic routing.
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


def perform_calculation(operation: str, a: float, b: float) -> dict:
    """Perform the requested mathematical operation."""
    try:
        if operation == "add":
            result = a + b
        elif operation == "subtract":
            result = a - b
        elif operation == "multiply":
            result = a * b
        elif operation == "divide":
            if b == 0:
                return {
                    "error": "Division by zero",
                    "operation": operation,
                    "operand_a": a,
                    "operand_b": b
                }
            result = a / b
        else:
            return {
                "error": f"Unknown operation: {operation}",
                "operation": operation,
                "operand_a": a,
                "operand_b": b
            }
        
        return {
            "result": result,
            "operation": operation,
            "operand_a": a,
            "operand_b": b,
            "formula": f"{a} {operation} {b} = {result}"
        }
    except Exception as e:
        return {
            "error": str(e),
            "operation": operation,
            "operand_a": a,
            "operand_b": b
        }


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
    print(f"🧮 CALCULATOR AGENT STARTING")
    print("="*70)
    print(f"Agent ID: {AGENT_ID}")
    print(f"Tenant ID: {TENANT_ID}")
    print(f"Bus URL: {BUS_URL}")
    print("="*70)
    
    try:
        async with websockets.connect(BUS_URL) as ws:
            # Step 1: Register with calculate capability
            print("\n" + "─"*70)
            print("📝 STEP 1: AGENT REGISTRATION")
            print("─"*70)
            print("Registering with the semantic bus...")
            
            # Define schema for calculator capability
            schemas = {
                "calculate": {
                    "type": "object",
                    "properties": {
                        "result": {"type": "number"},
                        "operation": {"type": "string"},
                        "operand_a": {"type": "number"},
                        "operand_b": {"type": "number"},
                        "formula": {"type": "string"},
                        "error": {"type": "string"}
                    },
                    "required": ["operation", "operand_a", "operand_b"]
                }
            }
            
            print("\n📋 Capabilities: calculate, math")
            print("🏷️  Tags: calculator, arithmetic, math-operations")
            print("📊 Schema: calculate (with result, operation, operands)")
            
            register_msg = create_envelope("agent.register", {
                "capabilities": ["calculate", "math"],
                "tags": ["calculator", "arithmetic", "math-operations"],
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
            print("⏳ WAITING FOR CALCULATION REQUESTS")
            print("─"*70)
            print("🧮 Supported operations:")
            print("   • Addition (add)")
            print("   • Subtraction (subtract)")
            print("   • Multiplication (multiply)")
            print("   • Division (divide)")
            print("\n💡 This agent is DIFFERENT from translation agents!")
            print("   It handles math, not language translation.")
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
                        for key, value in intent_data.items():
                            print(f"   • {key}: {value}")
                        
                        session_id = data.get("ephemeral_interface_id")
                        
                        # Step 3: Send intent.offer
                        print(f"\n" + "─"*70)
                        print("📤 STEP 3: SENDING OFFER")
                        print("─"*70)
                        print("🎁 Offer details:")
                        print("   • Schema: schema://calculate.result.v1")
                        print("   • View: view://math.result.v1")
                        print("   • Required params: operation, operand_a, operand_b")
                        print("   • Score: 0.99 (high confidence!)")
                        print("   • Operations: add, subtract, multiply, divide")
                        
                        offer_msg = create_envelope(
                            "intent.offer",
                            {
                                "schema_ids": ["schema://calculate.result.v1"],
                                "view_ids": ["view://math.result.v1"],
                                "requires": ["operation", "operand_a", "operand_b"],
                                "accepts": ["application/json"],
                                "score": 0.99,
                                "metadata": {
                                    "engine": "python-calculator",
                                    "operations": ["add", "subtract", "multiply", "divide"],
                                    "precision": "float64"
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
                        print("\n⏳ Waiting for calculation request...")
                    
                    # Step 5: Receive exec.request
                    elif msg_type == "exec.request":
                        print(f"\n" + "="*70)
                        print("📥 STEP 5: EXECUTION REQUEST RECEIVED!")
                        print("="*70)
                        
                        operation = data['payload'].get('operation', 'add')
                        operand_a = data['payload'].get('operand_a', 0)
                        operand_b = data['payload'].get('operand_b', 0)
                        
                        print(f"🧮 Calculation requested:")
                        print(f"   • Operation: {operation}")
                        print(f"   • Operand A: {operand_a}")
                        print(f"   • Operand B: {operand_b}")
                        print(f"   • Exec ID: {data['payload'].get('exec_id')}")
                        
                        # Step 6: Perform calculation and send exec.result
                        print(f"\n" + "─"*70)
                        print("🧮 STEP 6: PERFORMING CALCULATION")
                        print("─"*70)
                        
                        # Perform calculation
                        calc_result = perform_calculation(operation, operand_a, operand_b)
                        
                        if "error" in calc_result:
                            print(f"❌ Calculation error: {calc_result['error']}")
                        else:
                            print(f"✅ Calculation successful!")
                            print(f"   📐 Formula: {calc_result['formula']}")
                            print(f"   🎯 Result: {calc_result['result']}")
                        
                        print(f"\n📤 Sending result back to requester...")
                        
                        # Build result matching the "calculate" capability schema
                        result_msg = create_envelope(
                            "exec.result",
                            {
                                "result": calc_result,
                                "exec_id": data['payload'].get('exec_id')
                            },
                            ephemeral_interface_id=active_session_id,
                            schema_id=chosen_schema_id,
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
                        print("\n⏳ Ready for new calculation requests...")
                        print("─"*70 + "\n")
                    
                    # Ignore heartbeat acks
                    elif msg_type == "agent.heartbeat_ack":
                        pass
                    
                    # Log other messages
                    else:
                        print(f"\n   ? Received {msg_type}: {json.dumps(data, indent=2)}")
            
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
        print("👋 CALCULATOR AGENT SHUTTING DOWN")
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
