"""
Dot Hub Brain - Simple Claude + Horoscope Tool
Fast path for Hub requests. Jobs in context, one tool for horoscopes.

SIMPLE CLAUDE:
- Has all jobs in context (summary format for speed)
- One tool: get_horoscope (for fun)
- Answers job questions directly
- Redirects spend/people gracefully
- Fast (~2-3 seconds for most requests)
- Maintains conversation history for context

IMPORTANT: Claude returns job NUMBERS, not full objects.
Frontend matches job numbers to full objects from state.allJobs.
"""

import os
import json
import httpx
from anthropic import Anthropic

# ===================
# CONFIG
# ===================

ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY')
ANTHROPIC_MODEL = 'claude-sonnet-4-20250514'

# Horoscope service URL (internal call within Brain)
HOROSCOPE_SERVICE_URL = os.environ.get('HOROSCOPE_SERVICE_URL', 'https://dot-workers.up.railway.app')

# Load prompt
PROMPT_PATH = os.path.join(os.path.dirname(__file__), 'prompt_hub.txt')
with open(PROMPT_PATH, 'r') as f:
    HUB_PROMPT = f.read()

# Anthropic client
anthropic_client = Anthropic(
    api_key=ANTHROPIC_API_KEY,
    http_client=httpx.Client(timeout=30.0, follow_redirects=True)
)

# HTTP client for internal calls
http_client = httpx.Client(timeout=10.0)


# ===================
# TOOLS
# ===================

HOROSCOPE_TOOL = {
    "name": "get_horoscope",
    "description": "Get a horoscope for a star sign. Use when someone asks for their horoscope.",
    "input_schema": {
        "type": "object",
        "properties": {
            "sign": {
                "type": "string",
                "description": "The star sign (e.g., 'leo', 'aries', 'pisces')",
                "enum": ["aries", "taurus", "gemini", "cancer", "leo", "virgo", 
                        "libra", "scorpio", "sagittarius", "capricorn", "aquarius", "pisces"]
            }
        },
        "required": ["sign"]
    }
}


def call_horoscope_service(sign: str) -> dict:
    """
    Call the horoscope service to get a reading.
    """
    try:
        response = http_client.post(
            f"{HOROSCOPE_SERVICE_URL}/horoscope",
            json={"sign": sign.lower()}
        )
        if response.status_code == 200:
            return response.json()
        else:
            return {"error": f"Service returned {response.status_code}"}
    except Exception as e:
        print(f"[hub] Horoscope service error: {e}")
        return {"error": str(e)}


def handle_tool_call(tool_name: str, tool_input: dict) -> str:
    """
    Handle a tool call from Claude.
    """
    if tool_name == "get_horoscope":
        sign = tool_input.get("sign", "").lower()
        result = call_horoscope_service(sign)
        if "error" in result:
            return json.dumps({"error": result["error"]})
        return json.dumps({
            "intro": result.get("intro", ""),
            "sign": result.get("sign", sign.capitalize()),
            "horoscope": result.get("horoscope", "The stars are silent today."),
            "disclaimer": result.get("disclaimer", "")
        })
    
    return json.dumps({"error": f"Unknown tool: {tool_name}"})


# ===================
# HELPERS
# ===================

def _strip_markdown_json(content):
    """Strip markdown code blocks and preamble text from Claude's JSON response"""
    content = content.strip()
    
    # Handle markdown code blocks
    if content.startswith('```'):
        content = content.split('\n', 1)[1] if '\n' in content else content[3:]
    if content.endswith('```'):
        content = content.rsplit('```', 1)[0]
    
    # Find JSON object if there's preamble text
    # Look for first { and last }
    first_brace = content.find('{')
    last_brace = content.rfind('}')
    
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        content = content[first_brace:last_brace + 1]
    
    return content.strip()


def _format_jobs_for_context(jobs):
    """
    Format jobs list for Claude's context.
    Compact format to minimize tokens while giving Claude what it needs.
    """
    if not jobs:
        return "No active jobs."
    
    lines = []
    for job in jobs:
        # Core identifiers
        parts = [
            job.get('jobNumber', '???'),
            job.get('jobName', 'Untitled'),
            job.get('clientCode', '?'),
        ]
        
        # Status info
        stage = job.get('stage', '')
        status = job.get('status', '')
        if stage:
            parts.append(stage)
        if status and status != 'In Progress':
            parts.append(status)
        
        # With client flag
        if job.get('withClient'):
            parts.append('WITH CLIENT')
        
        # Dates
        if job.get('updateDue'):
            parts.append(f"Due:{job.get('updateDue')}")
        if job.get('liveDate'):
            parts.append(f"Live:{job.get('liveDate')}")
        
        # Days since update
        days_since = job.get('daysSinceUpdate', '')
        if days_since and days_since != '-':
            parts.append(f"({days_since})")
        
        # Latest update (truncated)
        update = job.get('update', '')
        if update:
            update_short = update[:60] + '...' if len(update) > 60 else update
            parts.append(f'"{update_short}"')
        
        lines.append(' | '.join(parts))
    
    return f"{len(jobs)} active jobs:\n" + "\n".join(lines)


def _format_meetings_for_context(meetings):
    """
    Format meetings for Claude's context.
    Compact format matching jobs style.
    """
    if not meetings:
        return "No upcoming meetings."
    
    lines = []
    for m in meetings:
        day_label = m.get('day', '').upper()  # "TODAY", "TOMORROW", "THURSDAY"
        parts = [
            m.get('startTime', ''), '–', m.get('endTime', ''),
            m.get('title', ''),
        ]
        if m.get('location'):
            parts.append(m.get('location'))
        if m.get('whose'):
            parts.append(f"Organiser:{m.get('whose')}")
        if m.get('attendees'):
            parts.append(f"Attendees:{m.get('attendees')}")
        lines.append(f"{day_label}: {' | '.join(p for p in parts if p)}")
    
    return f"{len(meetings)} meeting(s):\n" + "\n".join(lines)


# ===================
# MAIN HANDLER
# ===================

def handle_hub_request(data):
    """
    Handle a Hub chat request with Simple Claude + Horoscope tool.
    Jobs in context (summary format), one tool for horoscopes.
    Maintains conversation history for multi-turn context.
    
    Args:
        data: dict with content, jobs, senderName, sessionId, history
    
    Returns:
        dict with type, message, jobs (as job numbers), redirectTo, etc.
    """
    content = data.get('content', '')
    jobs = data.get('jobs', [])
    sender_name = data.get('senderName', 'there')
    history = data.get('history', [])  # Conversation history from frontend
    access_level = data.get('accessLevel', 'Client WIP')  # Default to most restricted
    
    # Fetch meetings only for Full access users
    if access_level == 'Full':
        from airtable import get_meetings
        meetings = get_meetings()
    else:
        meetings = []
    
    print(f"[hub] === SIMPLE CLAUDE + TOOLS ===")
    print(f"[hub] Question: {content}")
    print(f"[hub] Jobs in context: {len(jobs)}")
    print(f"[hub] Meetings in context: {len(meetings)}")
    print(f"[hub] History messages: {len(history)}")
    
    # Build context with jobs and meetings (summary only - NOT full JSON)
    jobs_context = _format_jobs_for_context(jobs)
    meetings_context = _format_meetings_for_context(meetings)
    
    # Current message with fresh job data
    current_message = f"""User: {sender_name}
Question: {content}

=== ACTIVE JOBS ===
{jobs_context}

=== MEETINGS ===
{meetings_context}
"""
    
    # Build messages array: history + current message
    messages = []
    
    # Add conversation history (without job context - keeps tokens down)
    for msg in history:
        role = msg.get('role', 'user')
        msg_content = msg.get('content', '')
        if role in ['user', 'assistant'] and msg_content:
            messages.append({'role': role, 'content': msg_content})
    
    # Add current message with fresh job context
    messages.append({'role': 'user', 'content': current_message})
    
    try:
        # First API call - may return tool use or direct response
        response = anthropic_client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=1500,
            temperature=0.1,
            system=HUB_PROMPT,
            messages=messages,
            tools=[HOROSCOPE_TOOL]
        )
        
        # Check if Claude wants to use a tool
        if response.stop_reason == "tool_use":
            # Find the tool use block
            tool_use_block = None
            for block in response.content:
                if block.type == "tool_use":
                    tool_use_block = block
                    break
            
            if tool_use_block:
                print(f"[hub] Tool call: {tool_use_block.name}")
                print(f"[hub] Tool input: {tool_use_block.input}")
                
                # Execute the tool
                tool_result = handle_tool_call(
                    tool_use_block.name, 
                    tool_use_block.input
                )
                
                # Add assistant's tool request and tool result to messages
                messages.append({
                    "role": "assistant",
                    "content": response.content
                })
                messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": tool_use_block.id,
                        "content": tool_result
                    }]
                })
                
                # Second API call to get final response
                response = anthropic_client.messages.create(
                    model=ANTHROPIC_MODEL,
                    max_tokens=1500,
                    temperature=0.1,
                    system=HUB_PROMPT,
                    messages=messages,
                    tools=[HOROSCOPE_TOOL]
                )
        
        # Extract text response
        result_text = ""
        for block in response.content:
            if hasattr(block, 'text'):
                result_text = block.text
                break
        
        result_text = _strip_markdown_json(result_text)
        result = json.loads(result_text)
        
        print(f"[hub] Type: {result.get('type')}")
        print(f"[hub] Message: {result.get('message', '')[:50]}...")
        if result.get('jobs'):
            print(f"[hub] Jobs returned: {result.get('jobs')}")
        
        return result
        
    except json.JSONDecodeError as e:
        print(f"[hub] JSON error: {e}")
        print(f"[hub] Raw response: {result_text[:200] if result_text else 'empty'}")
        # If Claude returned plain text, treat it as an answer
        if result_text and result_text.strip():
            return {
                'type': 'answer',
                'message': result_text.strip(),
                'jobs': None,
                'nextPrompt': None
            }
        return {
            'type': 'answer',
            'message': "Sorry, I got in a muddle over that one.",
            'jobs': None,
            'nextPrompt': "Try asking another way?"
        }
        
    except Exception as e:
        print(f"[hub] Error: {e}")
        import traceback
        traceback.print_exc()
        return {
            'type': 'answer', 
            'message': "Sorry, I got in a muddle over that one.",
            'jobs': None,
            'nextPrompt': "Try asking another way?"
        }
