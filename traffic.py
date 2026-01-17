"""
Dot Traffic 2.0 - Routing Logic
Builds context, calls Claude, parses response
"""

import os
import re
import json
import httpx
from anthropic import Anthropic

# ===================
# CONFIG
# ===================

ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY')
ANTHROPIC_MODEL = 'claude-sonnet-4-20250514'

VALID_CLIENT_CODES = ['ONE', 'ONS', 'ONB', 'SKY', 'TOW', 'FIS', 'FST', 'WKA', 'HUN', 'LAB', 'EON', 'OTH']

CLIENT_NAME_MAPPING = {
    'one nz': 'ONE',
    'one': 'ONE',
    'ons': 'ONS',
    'onb': 'ONB',
    'simplification': 'ONS',
    'one nz simplification': 'ONS',
    'one nz - simplification': 'ONS',
    'business': 'ONB',
    'one nz business': 'ONB',
    'one nz - business': 'ONB',
    'sky': 'SKY',
    'sky tv': 'SKY',
    'tower': 'TOW',
    'tower insurance': 'TOW',
    'fisher funds': 'FIS',
    'fisherfunds': 'FIS',
    'firestop': 'FST',
    'whakarongorau': 'WKA',
    'healthline': 'WKA',
    'labour': 'LAB',
    'eon fibre': 'EON',
    'eonfibre': 'EON',
    'hunch': 'HUN'
}

# Load prompt
PROMPT_PATH = os.path.join(os.path.dirname(__file__), 'prompt.txt')
with open(PROMPT_PATH, 'r') as f:
    TRAFFIC_PROMPT = f.read()

# Anthropic client
anthropic_client = Anthropic(
    api_key=ANTHROPIC_API_KEY,
    http_client=httpx.Client(timeout=60.0, follow_redirects=True)
)


# ===================
# EXTRACTION HELPERS
# ===================

def extract_job_number(text):
    """
    Extract job number from text (e.g., 'TOW 023').
    Pattern: 3 letters + space + 3 digits
    Returns job number string or None.
    """
    if not text:
        return None
    
    # Look for pattern: 3 letters + space + 3 digits
    match = re.search(r'\b([A-Z]{3})\s+(\d{3})\b', text.upper())
    if match:
        code = match.group(1)
        number = match.group(2)
        if code in VALID_CLIENT_CODES:
            return f"{code} {number}"
    
    # Also check for underscore variant: ONE_125
    match = re.search(r'\b([A-Z]{3})_(\d{3})\b', text.upper())
    if match:
        code = match.group(1)
        number = match.group(2)
        if code in VALID_CLIENT_CODES:
            return f"{code} {number}"
    
    return None


def extract_client_code(text):
    """
    Extract client code from text.
    Checks for direct codes (ONS, SKY) and client names (Tower Insurance).
    Returns client code or None.
    """
    if not text:
        return None
    
    text_lower = text.lower()
    text_upper = text.upper()
    
    # First check for direct client codes as standalone words
    for code in VALID_CLIENT_CODES:
        if re.search(r'\b' + code + r'\b', text_upper):
            return code
    
    # Then check for client names in the text
    for name, code in CLIENT_NAME_MAPPING.items():
        if name in text_lower:
            return code
    
    return None


def strip_markdown_json(content):
    """Strip markdown code blocks from Claude's JSON response"""
    content = content.strip()
    if content.startswith('```'):
        content = content.split('\n', 1)[1] if '\n' in content else content[3:]
    if content.endswith('```'):
        content = content.rsplit('```', 1)[0]
    return content.strip()


# ===================
# MAIN ROUTING FUNCTION
# ===================

def route_email(email_data, active_jobs=None):
    """
    Route an email through Claude.
    
    Args:
        email_data: dict with email fields (subject, content, sender, etc.)
        active_jobs: list of active jobs for the identified client
    
    Returns:
        dict with routing decision from Claude
    """
    
    # Extract fields from email data
    subject = email_data.get('subjectLine', '')
    content = email_data.get('emailContent', '')
    sender_email = email_data.get('senderEmail', '')
    sender_name = email_data.get('senderName', '')
    all_recipients = email_data.get('allRecipients', [])
    has_attachments = email_data.get('hasAttachments', False)
    attachment_names = email_data.get('attachmentNames', [])
    source = email_data.get('source', 'email')
    
    # Pre-extract job number and client code
    job_number = extract_job_number(subject)
    if not job_number:
        job_number = extract_job_number(content)
    
    # Also check attachment names for job number
    if not job_number and attachment_names:
        for filename in attachment_names:
            job_number = extract_job_number(filename)
            if job_number:
                break
    
    client_code = None
    if job_number:
        client_code = job_number.split()[0]
    
    if not client_code:
        client_code = extract_client_code(subject)
        if not client_code:
            client_code = extract_client_code(content)
    
    # Debug logging
    print(f"[traffic] === ROUTING DEBUG ===")
    print(f"[traffic] Subject: {subject}")
    print(f"[traffic] Sender: {sender_email}")
    print(f"[traffic] Extracted job_number: {job_number}")
    print(f"[traffic] Extracted client_code: {client_code}")
    print(f"[traffic] Active jobs received: {len(active_jobs) if active_jobs else 0}")
    if active_jobs:
        for job in active_jobs:
            print(f"[traffic]   - {job.get('jobNumber')}: {job.get('jobName')}")
    
    # Format active jobs for prompt
    active_jobs_text = "No active jobs found"
    if active_jobs:
        active_jobs_text = "\n".join([
            f"- {job['jobNumber']} - {job['jobName']}: {job.get('description', '')}"
            for job in active_jobs
        ])
    
    # Build the full context for Claude
    full_content = f"""Source: {source}
Subject: {subject}

From: {sender_name} <{sender_email}>
Recipients: {', '.join(all_recipients) if isinstance(all_recipients, list) else all_recipients}
Has Attachments: {has_attachments}
Attachment Names: {', '.join(attachment_names) if isinstance(attachment_names, list) else attachment_names}

Extracted job number: {job_number if job_number else 'None found'}
Client code: {client_code if client_code else 'Unknown'}

Active jobs for this client:
{active_jobs_text}

Message content:
{content}"""
    
    # Call Claude
    try:
        response = anthropic_client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=1500,
            temperature=0.1,
            system=TRAFFIC_PROMPT,
            messages=[
                {'role': 'user', 'content': full_content}
            ]
        )
        
        result_text = response.content[0].text
        result_text = strip_markdown_json(result_text)
        routing = json.loads(result_text)
        
        # Debug logging - Claude's decision
        print(f"[traffic] === CLAUDE DECISION ===")
        print(f"[traffic] Route: {routing.get('route')}")
        print(f"[traffic] Confidence: {routing.get('confidence')}")
        print(f"[traffic] Client: {routing.get('clientCode')} / {routing.get('clientName')}")
        print(f"[traffic] Job: {routing.get('jobNumber')}")
        print(f"[traffic] Reason: {routing.get('reason')}")
        if routing.get('possibleJobs'):
            print(f"[traffic] Possible jobs: {len(routing.get('possibleJobs'))}")
        
        return routing
        
    except json.JSONDecodeError as e:
        print(f"[traffic] Claude returned invalid JSON: {e}")
        print(f"[traffic] Raw response: {result_text if 'result_text' in dir() else 'No response'}")
        return {
            'route': 'error',
            'confidence': 'low',
            'reason': 'Claude returned invalid JSON',
            'error': str(e)
        }
    
    except Exception as e:
        print(f"[traffic] Error calling Claude: {e}")
        return {
            'route': 'error',
            'confidence': 'low',
            'reason': 'Error calling Claude',
            'error': str(e)
        }
