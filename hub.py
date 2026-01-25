"""
Dot Hub Brain - Simple Claude
Fast path for Hub requests. No tools, jobs in context.

SIMPLE CLAUDE:
- Has all jobs in context (summary format for speed)
- No tools to call
- Answers job questions directly
- Redirects spend/people gracefully
- Fast (~2-3 seconds)
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

# Load prompt
PROMPT_PATH = os.path.join(os.path.dirname(__file__), 'prompt_hub.txt')
with open(PROMPT_PATH, 'r') as f:
    HUB_PROMPT = f.read()

# Anthropic client
anthropic_client = Anthropic(
    api_key=ANTHROPIC_API_KEY,
    http_client=httpx.Client(timeout=30.0, follow_redirects=True)
)


# ===================
# HELPERS
# ===================

def _strip_markdown_json(content):
    """Strip markdown code blocks from Claude's JSON response"""
    content = content.strip()
    if content.startswith('```'):
        content = content.split('\n', 1)[1] if '\n' in content else content[3:]
    if content.endswith('```'):
        content = content.rsplit('```', 1)[0]
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


# ===================
# MAIN HANDLER
# ===================

def handle_hub_request(data):
    """
    Handle a Hub chat request with Simple Claude.
    No tools - just jobs in context (summary format).
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
    
    print(f"[hub] === SIMPLE CLAUDE ===")
    print(f"[hub] Question: {content}")
    print(f"[hub] Jobs in context: {len(jobs)}")
    print(f"[hub] History messages: {len(history)}")
    
    # Build context with jobs (summary only - NOT full JSON)
    jobs_context = _format_jobs_for_context(jobs)
    
    # Current message with fresh job data
    current_message = f"""User: {sender_name}
Question: {content}

=== ACTIVE JOBS ===
{jobs_context}
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
        response = anthropic_client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=1500,
            temperature=0.1,
            system=HUB_PROMPT,
            messages=messages
        )
        
        result_text = response.content[0].text
        result_text = _strip_markdown_json(result_text)
        
        result = json.loads(result_text)
        
        print(f"[hub] Type: {result.get('type')}")
        print(f"[hub] Message: {result.get('message', '')[:50]}...")
        if result.get('jobs'):
            print(f"[hub] Jobs returned: {result.get('jobs')}")
        
        return result
        
    except json.JSONDecodeError as e:
        print(f"[hub] JSON error: {e}")
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
