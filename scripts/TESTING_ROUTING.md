# Testing Semantic Routing with Calculator Agent

This directory contains test agents demonstrating semantic routing capabilities of the bus.

## Test Agents

### Translation Agents
- **test_agent_translator.py** - Provider that handles translation requests
- **test_agent_intent_translation.py** - Requester that sends translation intents

### Calculator Agents (NEW)
- **test_agent_calculator.py** - Provider that handles mathematical calculations
- **test_agent_intent_calculation.py** - Requester that sends calculation intents

## Testing Semantic Routing

The semantic bus should correctly route requests to the appropriate agent based on capability matching, not just any available agent.

### Scenario 1: Single Agent Type
Test that each agent works independently:

```bash
# Terminal 1: Start bus
cd semantic-bus
uvicorn src.transport.app:app --reload

# Terminal 2: Start translator
python scripts/test_agent_translator.py

# Terminal 3: Send translation request
python scripts/test_agent_intent_translation.py
```

### Scenario 2: Both Agents Running (Semantic Routing Test)
This is the key test! Run BOTH the translator AND calculator agents simultaneously, then send different types of requests. The bus should route each request to the correct agent.

```bash
# Terminal 1: Start bus
cd semantic-bus
uvicorn src.transport.app:app --reload

# Terminal 2: Start translator agent
python scripts/test_agent_translator.py

# Terminal 3: Start calculator agent
python scripts/test_agent_calculator.py

# Terminal 4: Send calculation request
# Should route to calculator agent ONLY, not translator
python scripts/test_agent_intent_calculation.py

# Terminal 5: Send translation request
# Should route to translator agent ONLY, not calculator
python scripts/test_agent_intent_translation.py
```

## Expected Behavior

When both agents are running:
- **Translation requests** (`capability: "translate"`) → routed to **translator agent**
- **Calculation requests** (`capability: "calculate"`) → routed to **calculator agent**

The semantic matcher in the bus should match based on:
1. Exact capability match
2. Semantic similarity of intent content
3. Agent metadata and tags

## What to Observe

### In the Calculator Agent Terminal
- Should receive intent.deliver for calculation requests
- Should NOT receive translation requests
- Should perform math operations (add, subtract, multiply, divide)

### In the Translator Agent Terminal
- Should receive intent.deliver for translation requests
- Should NOT receive calculation requests
- Should perform text translations

### In the Bus Logs
- Look for semantic matching scores
- Verify correct agent selection
- Check that intents are routed to appropriate agents only

## Calculator Agent Details

**Capabilities**: `calculate`, `math`
**Operations**: 
- `add` - Addition
- `subtract` - Subtraction
- `multiply` - Multiplication
- `divide` - Division (with zero-division protection)

**Request Format**:
```json
{
  "operation": "multiply",
  "operand_a": 42,
  "operand_b": 13
}
```

**Response Format**:
```json
{
  "result": 546,
  "operation": "multiply",
  "operand_a": 42,
  "operand_b": 13,
  "formula": "42 multiply 13 = 546"
}
```
