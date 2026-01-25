"""
Dot Hub Brain - Simple Claude
Fast path for Hub requests. No tools, jobs in context.

SIMPLE CLAUDE:
- Has all jobs in context
- No tools to call
- Answers job questions directly
- Redirects spend/people gracefully
- Fast (~2-3 seconds)
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
    """Format jobs list for Claude's context"""
    if not jobs:
        return "No active jobs provided."
    
    lines = []
    for job in jobs:
        line = f"- {job.get('jobNumber', '???')} | {job.get('jobName', 'Untitled')}"
        line += f" | {job.get('stage', '?')} | {job.get('status', '?')}"
        if job.get('withClient'):
            line += " | WITH CLIENT"
        if job.get('updateDue'):
            line += f" | Due: {job.get('updateDue')}"
        if job.get('update'):
            line += f" | Latest: {job.get('update')[:50]}..."
        lines.append(line)
    
    return f"{len(jobs)} active jobs:\n" + "\n".join(lines)


# ===================
# MAIN HANDLER
# ===================

def handle_hub_request(data):
    """
    Handle a Hub chat request with Simple Claude.
    No tools - just jobs in context.
    
    Args:
        data: dict with content, jobs, senderName, sessionId
    
    Returns:
        dict with type, message, jobs, redirectTo, etc.
    """
    content = data.get('content', '')
    jobs = data.get('jobs', [])
    sender_name = data.get('senderName', 'there')
    
    print(f"[hub] === SIMPLE CLAUDE ===")
    print(f"[hub] Question: {content}")
    print(f"[hub] Jobs in context: {len(jobs)}")
    
    # Build context with jobs
    jobs_context = _format_jobs_for_context(jobs)
    
    # Also include full job data as JSON for Claude to reference
    jobs_json = json.dumps(jobs, indent=2) if jobs else "[]"
    
    full_content = f"""User: {sender_name}
Question: {content}

=== ACTIVE JOBS (Summary) ===
{jobs_context}

=== ACTIVE JOBS (Full Data) ===
{jobs_json}
"""
    
    try:
        response = anthropic_client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=1500,
            temperature=0.1,
            system=HUB_PROMPT,
            messages=[{'role': 'user', 'content': full_content}]
        )
        
        result_text = response.content[0].text
        result_text = _strip_markdown_json(result_text)
        
        result = json.loads(result_text)
        
        print(f"[hub] Type: {result.get('type')}")
        print(f"[hub] Message: {result.get('message', '')[:50]}...")
        
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
