"""
Dot Traffic - Unified Brain
Routes requests, answers questions, calls workers.

UNIFIED: Handles both email and hub sources.
- Email: PA Listener -> /traffic -> workers
- Hub: Ask Dot -> /traffic -> response

One brain. Two inputs. Same Dot.
"""

import os
import re
import json
import time
import requests
import httpx
from datetime import datetime
from anthropic import Anthropic

# ===================
# CONFIG
# ===================

ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY')
AIRTABLE_API_KEY = os.environ.get('AIRTABLE_API_KEY')
AIRTABLE_BASE_ID = os.environ.get('AIRTABLE_BASE_ID', 'app8CI7NAZqhQ4G1Y')

ANTHROPIC_MODEL = 'claude-sonnet-4-20250514'

VALID_CLIENT_CODES = ['ONE', 'ONS', 'ONB', 'SKY', 'TOW', 'FIS', 'FST', 'WKA', 'HUN', 'LAB', 'EON', 'OTH']

# Airtable headers
AIRTABLE_HEADERS = {
    'Authorization': f'Bearer {AIRTABLE_API_KEY}',
    'Content-Type': 'application/json'
}

def get_airtable_url(table):
    return f'https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{table}'

# Load prompt (unified version)
PROMPT_PATH = os.path.join(os.path.dirname(__file__), 'prompt_unified.txt')
# Fallback to old prompt if unified doesn't exist yet
if not os.path.exists(PROMPT_PATH):
    PROMPT_PATH = os.path.join(os.path.dirname(__file__), 'prompt.txt')
with open(PROMPT_PATH, 'r') as f:
    TRAFFIC_PROMPT = f.read()

# Anthropic client
anthropic_client = Anthropic(
    api_key=ANTHROPIC_API_KEY,
    http_client=httpx.Client(timeout=60.0, follow_redirects=True)
)


# ===================
# CONVERSATION MEMORY (Hub only)
# ===================

conversations = {}
SESSION_TIMEOUT = 30 * 60  # 30 minutes

def get_conversation(session_id):
    """Get or create conversation history for a session"""
    now = time.time()
    
    # Clean up old sessions
    expired = [sid for sid, data in conversations.items() if now - data['last_active'] > SESSION_TIMEOUT]
    for sid in expired:
        del conversations[sid]
    
    if session_id not in conversations:
        conversations[session_id] = {
            'messages': [],
            'last_active': now
        }
    else:
        conversations[session_id]['last_active'] = now
    
    return conversations[session_id]

def add_to_conversation(session_id, role, content):
    """Add a message to conversation history"""
    conv = get_conversation(session_id)
    conv['messages'].append({'role': role, 'content': content})
    
    # Keep only last 10 exchanges (20 messages)
    if len(conv['messages']) > 20:
        conv['messages'] = conv['messages'][-20:]

def clear_conversation(session_id):
    """Clear conversation history for a session"""
    if session_id in conversations:
        del conversations[session_id]
    return True


# ===================
# TOOLS FOR DOT
# ===================

def tool_search_people(client_code=None, search_term=None):
    """Search People table"""
    try:
        url = get_airtable_url('People')
        
        filters = ["{Active} = TRUE()"]
        if client_code:
            # Handle One NZ divisions
            if client_code in ['ONE', 'ONB', 'ONS']:
                filters.append("OR({Client Link} = 'ONE', {Client Link} = 'ONB', {Client Link} = 'ONS')")
            else:
                filters.append(f"{{Client Link}} = '{client_code}'")
        
        params = {
            'filterByFormula': f"AND({', '.join(filters)})" if len(filters) > 1 else filters[0]
        }
        
        all_people = []
        offset = None
        
        while True:
            if offset:
                params['offset'] = offset
            
            response = requests.get(url, headers=AIRTABLE_HEADERS, params=params)
            response.raise_for_status()
            data = response.json()
            
            for record in data.get('records', []):
                fields = record.get('fields', {})
                name = fields.get('Name', fields.get('Full name', ''))
                if not name:
                    continue
                
                if search_term:
                    searchable = f"{name} {fields.get('Email Address', '')}".lower()
                    if search_term.lower() not in searchable:
                        continue
                
                all_people.append({
                    'name': name,
                    'email': fields.get('Email Address', ''),
                    'phone': fields.get('Phone Number', ''),
                    'clientCode': fields.get('Client Link', '')
                })
            
            offset = data.get('offset')
            if not offset:
                break
        
        return {'count': len(all_people), 'people': all_people}
    
    except Exception as e:
        return {'error': str(e)}


def tool_get_client_detail(client_code):
    """Get detailed client info"""
    try:
        url = get_airtable_url('Clients')
        params = {
            'filterByFormula': f"{{Client code}} = '{client_code}'",
            'maxRecords': 1
        }
        response = requests.get(url, headers=AIRTABLE_HEADERS, params=params)
        response.raise_for_status()
        
        records = response.json().get('records', [])
        if not records:
            return {'error': f'Client {client_code} not found'}
        
        fields = records[0].get('fields', {})
        
        def parse_currency(val):
            if isinstance(val, (int, float)):
                return val
            if isinstance(val, str):
                return int(val.replace('$', '').replace(',', '') or 0)
            return 0
        
        rollover = fields.get('Rollover Credit', 0)
        if isinstance(rollover, list):
            rollover = rollover[0] if rollover else 0
        rollover = parse_currency(rollover)
        
        return {
            'code': client_code,
            'name': fields.get('Clients', ''),
            'yearEnd': fields.get('Year end', ''),
            'currentQuarter': fields.get('Current Quarter', ''),
            'monthlyCommitted': parse_currency(fields.get('Monthly Committed', 0)),
            'quarterlyCommitted': parse_currency(fields.get('Quarterly Committed', 0)),
            'thisMonth': parse_currency(fields.get('This month', 0)),
            'thisQuarter': parse_currency(fields.get('This Quarter', 0)),
            'rolloverCredit': rollover,
            'nextJobNumber': fields.get('Next Job #', '')
        }
    
    except Exception as e:
        return {'error': str(e)}


def tool_get_spend_summary(client_code, period='this_month'):
    """Get spend summary for a client"""
    try:
        clients_url = get_airtable_url('Clients')
        clients_response = requests.get(clients_url, headers=AIRTABLE_HEADERS)
        clients_response.raise_for_status()
        
        client_info = None
        for record in clients_response.json().get('records', []):
            fields = record.get('fields', {})
            if fields.get('Client code', '') == client_code:
                def parse_currency(val):
                    if isinstance(val, (int, float)):
                        return float(val)
                    if isinstance(val, str):
                        return float(val.replace('$', '').replace(',', '') or 0)
                    if isinstance(val, list):
                        return float(val[0]) if val else 0
                    return 0
                
                monthly = parse_currency(fields.get('Monthly Committed', 0))
                rollover = parse_currency(fields.get('Rollover Credit', 0))
                rollover_use = fields.get('Rollover use', '')
                
                client_info = {
                    'name': fields.get('Clients', ''),
                    'code': client_code,
                    'monthlyBudget': monthly,
                    'quarterlyBudget': monthly * 3,
                    'currentQuarter': fields.get('Current Quarter', ''),
                    'rollover': rollover,
                    'rolloverUse': rollover_use,
                    'JAN-MAR': parse_currency(fields.get('JAN-MAR', 0)),
                    'APR-JUN': parse_currency(fields.get('APR-JUN', 0)),
                    'JUL-SEP': parse_currency(fields.get('JUL-SEP', 0)),
                    'OCT-DEC': parse_currency(fields.get('OCT-DEC', 0)),
                    'thisMonth': parse_currency(fields.get('This month', 0)),
                }
                break
        
        if not client_info:
            return {'error': f'Client {client_code} not found'}
        
        now = datetime.now()
        current_month_num = now.month
        
        calendar_quarters = {
            1: 'JAN-MAR', 2: 'JAN-MAR', 3: 'JAN-MAR',
            4: 'APR-JUN', 5: 'APR-JUN', 6: 'APR-JUN',
            7: 'JUL-SEP', 8: 'JUL-SEP', 9: 'JUL-SEP',
            10: 'OCT-DEC', 11: 'OCT-DEC', 12: 'OCT-DEC'
        }
        current_cal_quarter = calendar_quarters[current_month_num]
        
        prev_quarters = {
            'JAN-MAR': 'OCT-DEC',
            'APR-JUN': 'JAN-MAR',
            'JUL-SEP': 'APR-JUN',
            'OCT-DEC': 'JUL-SEP'
        }
        last_cal_quarter = prev_quarters[current_cal_quarter]
        
        if period == 'this_quarter':
            quarter_key = current_cal_quarter
            period_label = client_info['currentQuarter']
        elif period == 'last_quarter':
            quarter_key = last_cal_quarter
            current_q_num = int(client_info['currentQuarter'].replace('Q', '') or 1)
            last_q_num = current_q_num - 1 if current_q_num > 1 else 4
            period_label = f'Q{last_q_num}'
        elif period in ['JAN-MAR', 'APR-JUN', 'JUL-SEP', 'OCT-DEC']:
            quarter_key = period
            period_label = period
        elif period == 'this_month':
            return {
                'client': client_info['name'],
                'clientCode': client_code,
                'period': now.strftime('%B'),
                'budget': client_info['monthlyBudget'],
                'spent': client_info['thisMonth'],
                'remaining': client_info['monthlyBudget'] - client_info['thisMonth'],
                'percentUsed': round((client_info['thisMonth'] / client_info['monthlyBudget'] * 100) if client_info['monthlyBudget'] > 0 else 0)
            }
        else:
            quarter_key = current_cal_quarter
            period_label = client_info['currentQuarter']
        
        spent = client_info.get(quarter_key, 0)
        budget = client_info['quarterlyBudget']
        
        if client_info['rolloverUse'] == quarter_key and client_info['rollover'] > 0:
            budget += client_info['rollover']
        
        return {
            'client': client_info['name'],
            'clientCode': client_code,
            'period': period_label,
            'budget': budget,
            'spent': spent,
            'remaining': budget - spent,
            'percentUsed': round((spent / budget * 100) if budget > 0 else 0),
            'rolloverApplied': client_info['rolloverUse'] == quarter_key and client_info['rollover'] > 0,
            'rolloverAmount': client_info['rollover'] if client_info['rolloverUse'] == quarter_key else 0
        }
    
    except Exception as e:
        return {'error': str(e)}


def tool_reserve_job_number(client_code):
    """Reserve the next job number for a client"""
    try:
        url = get_airtable_url('Clients')
        params = {
            'filterByFormula': f"{{Client code}} = '{client_code}'",
            'maxRecords': 1
        }
        response = requests.get(url, headers=AIRTABLE_HEADERS, params=params)
        response.raise_for_status()
        
        records = response.json().get('records', [])
        if not records:
            return {'error': f'Client {client_code} not found'}
        
        record = records[0]
        record_id = record.get('id')
        fields = record.get('fields', {})
        client_name = fields.get('Clients', client_code)
        
        next_num_str = fields.get('Next Job #', '')
        if not next_num_str:
            return {'error': f'No job number sequence configured for {client_code}'}
        
        try:
            next_num = int(next_num_str)
        except ValueError:
            return {'error': f'Invalid job number format: {next_num_str}'}
        
        reserved_job_number = f"{client_code} {next_num:03d}"
        new_next_num = f"{next_num + 1:03d}"
        
        update_response = requests.patch(
            f"{url}/{record_id}",
            headers=AIRTABLE_HEADERS,
            json={'fields': {'Next Job #': new_next_num}}
        )
        update_response.raise_for_status()
        
        return {
            'success': True,
            'clientCode': client_code,
            'clientName': client_name,
            'reservedJobNumber': reserved_job_number,
            'nextJobNumber': new_next_num
        }
    
    except Exception as e:
        return {'error': str(e)}


# Tool definitions for Claude API
CLAUDE_TOOLS = [
    {
        "name": "search_people",
        "description": "Search for contacts/people in the database. Use this when asked about client contacts, email addresses, phone numbers, or how many people work at a client.",
        "input_schema": {
            "type": "object",
            "properties": {
                "client_code": {
                    "type": "string",
                    "description": "Filter by client code (e.g., 'SKY', 'TOW', 'ONE'). Optional."
                },
                "search_term": {
                    "type": "string",
                    "description": "Search for a specific person by name or email. Optional."
                }
            },
            "required": []
        }
    },
    {
        "name": "get_client_detail",
        "description": "Get detailed information about a client including their budget, quarter, commercial setup, and next job number.",
        "input_schema": {
            "type": "object",
            "properties": {
                "client_code": {
                    "type": "string",
                    "description": "The client code (e.g., 'SKY', 'TOW', 'ONE')"
                }
            },
            "required": ["client_code"]
        }
    },
    {
        "name": "get_spend_summary",
        "description": "Get spend/budget summary for a client. Use this when asked about how much has been spent, budget remaining, or financial tracking.",
        "input_schema": {
            "type": "object",
            "properties": {
                "client_code": {
                    "type": "string",
                    "description": "The client code (e.g., 'SKY', 'TOW', 'ONE')"
                },
                "period": {
                    "type": "string",
                    "description": "Time period: 'this_month', 'this_quarter', or 'last_quarter'"
                }
            },
            "required": ["client_code"]
        }
    },
    {
        "name": "reserve_job_number",
        "description": "Reserve and lock in the next job number for a client. This WRITES to the database - only use when the user confirms they want to reserve a number.",
        "input_schema": {
            "type": "object",
            "properties": {
                "client_code": {
                    "type": "string",
                    "description": "The client code (e.g., 'SKY', 'TOW', 'ONE')"
                }
            },
            "required": ["client_code"]
        }
    },
    {
        "name": "get_active_jobs",
        "description": "Get all active (non-completed) jobs for a specific client. Use this when you know which client and need to see their jobs. Returns job numbers, names, descriptions, stage, status, and due dates.",
        "input_schema": {
            "type": "object",
            "properties": {
                "client_code": {
                    "type": "string",
                    "description": "The client code (e.g., 'SKY', 'TOW', 'LAB')"
                }
            },
            "required": ["client_code"]
        }
    },
    {
        "name": "get_all_active_jobs",
        "description": "Get ALL active jobs across ALL clients in one call. Use this for cross-client queries like 'What's due today?' or 'What's on this week?' - returns ~20 jobs total. More efficient than calling get_active_jobs multiple times.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "get_job_by_number",
        "description": "Get a specific job by its job number (e.g., 'LAB 055' or 'SKY 042'). Use this when you have an exact job number and need its details.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_number": {
                    "type": "string",
                    "description": "The job number (e.g., 'LAB 055', 'SKY 042')"
                }
            },
            "required": ["job_number"]
        }
    }
]


def execute_tool(tool_name, tool_input):
    """Execute a tool and return results"""
    print(f"[traffic] Executing tool: {tool_name} with input: {tool_input}")
    
    if tool_name == "search_people":
        result = tool_search_people(
            client_code=tool_input.get('client_code'),
            search_term=tool_input.get('search_term')
        )
    elif tool_name == "get_client_detail":
        result = tool_get_client_detail(tool_input.get('client_code'))
    elif tool_name == "get_spend_summary":
        result = tool_get_spend_summary(
            client_code=tool_input.get('client_code'),
            period=tool_input.get('period', 'this_month')
        )
    elif tool_name == "reserve_job_number":
        result = tool_reserve_job_number(tool_input.get('client_code'))
    elif tool_name == "get_active_jobs":
        # Import here to avoid circular import
        import airtable
        jobs = airtable.get_active_jobs(tool_input.get('client_code'))
        result = {'jobs': jobs, 'count': len(jobs)}
    elif tool_name == "get_all_active_jobs":
        import airtable
        jobs = airtable.get_all_active_jobs()
        result = {'jobs': jobs, 'count': len(jobs)}
    elif tool_name == "get_job_by_number":
        import airtable
        job = airtable.get_job_by_number(tool_input.get('job_number'))
        if job:
            result = {'job': job, 'found': True}
        else:
            result = {'job': None, 'found': False, 'message': f"Job {tool_input.get('job_number')} not found"}
    else:
        result = {'error': f'Unknown tool: {tool_name}'}
    
    print(f"[traffic] Tool result: {result}")
    return result


# ===================
# EXTRACTION HELPERS
# ===================

def extract_job_number(text):
    """
    Extract job number from text (e.g., 'TOW 023').
    Pattern: 3 letters + space + 3 digits
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

def route_request(request_data, active_jobs=None):
    """
    Route a request through Claude - unified for email and hub.
    
    Args:
        request_data: dict with request fields (content, source, sender, etc.)
        active_jobs: optional list of active jobs
    
    Returns:
        dict with routing decision from Claude (type, message, route, jobs, etc.)
    """
    
    # Determine source
    source = request_data.get('source', 'email')
    
    # Extract fields - accept various naming conventions
    content = request_data.get('content') or request_data.get('body') or request_data.get('emailContent', '')
    subject = request_data.get('subject') or request_data.get('subjectLine', '')
    sender_email = request_data.get('senderEmail') or request_data.get('from', '')
    sender_name = request_data.get('senderName', '')
    recipients = request_data.get('recipients') or request_data.get('to') or request_data.get('allRecipients', [])
    has_attachments = request_data.get('hasAttachments', False)
    attachment_names = request_data.get('attachmentNames', [])
    session_id = request_data.get('sessionId', None)
    
    # Extract job number hint (regex is fine for structured data)
    job_number = extract_job_number(subject)
    if not job_number:
        job_number = extract_job_number(content)
    if not job_number and attachment_names:
        for filename in attachment_names:
            job_number = extract_job_number(filename)
            if job_number:
                break
    
    # Debug logging
    print(f"[traffic] === ROUTING DEBUG ===")
    print(f"[traffic] Source: {source}")
    print(f"[traffic] Content: {content[:100]}..." if len(content) > 100 else f"[traffic] Content: {content}")
    print(f"[traffic] Sender: {sender_email}")
    print(f"[traffic] Job number (regex): {job_number}")
    
    # Format active jobs for prompt
    active_jobs_text = "No active jobs provided"
    if active_jobs:
        active_jobs_text = "\n".join([
            f"- {job['jobNumber']} - {job['jobName']}: {job.get('description', '')} (Stage: {job.get('stage', 'Unknown')}, Status: {job.get('status', 'Unknown')})"
            for job in active_jobs
        ])
        print(f"[traffic] Active jobs provided: {len(active_jobs)}")
    
    # Build context for Claude
    if source == 'hub':
        # Hub: simpler context, just the message
        full_content = f"""Source: hub
User: {sender_name} <{sender_email}>

Message:
{content}

Job number found in text: {job_number if job_number else 'None'}

Active jobs for reference:
{active_jobs_text}"""
    else:
        # Email: full email context
        full_content = f"""Source: email
Subject: {subject}

From: {sender_name} <{sender_email}>
Recipients: {', '.join(recipients) if isinstance(recipients, list) else recipients}
Has Attachments: {has_attachments}
Attachment Names: {', '.join(attachment_names) if isinstance(attachment_names, list) else attachment_names}

Job number found in text: {job_number if job_number else 'None'}

Active jobs for reference:
{active_jobs_text}

Email content:
{content}"""
    
    # Build messages array
    messages = []
    
    # Add conversation history for hub sessions
    if source == 'hub' and session_id:
        conv = get_conversation(session_id)
        for msg in conv['messages'][-10:]:  # Last 10 messages
            messages.append(msg)
    
    # Add current message
    messages.append({'role': 'user', 'content': full_content})
    
    # Call Claude
    try:
        response = anthropic_client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=1500,
            temperature=0.1,
            system=TRAFFIC_PROMPT,
            tools=CLAUDE_TOOLS,
            messages=messages
        )
        
        # Handle tool use - loop until Claude is done (max 5 rounds to prevent runaway)
        tool_rounds = 0
        max_tool_rounds = 5
        
        while response.stop_reason == 'tool_use' and tool_rounds < max_tool_rounds:
            tool_rounds += 1
            print(f"[traffic] Tool round {tool_rounds}")
            
            tool_results = []
            content_blocks = response.content
            
            for block in content_blocks:
                if block.type == 'tool_use':
                    print(f"[traffic] Executing tool: {block.name}")
                    tool_result = execute_tool(block.name, block.input)
                    tool_results.append({
                        'type': 'tool_result',
                        'tool_use_id': block.id,
                        'content': json.dumps(tool_result)
                    })
            
            # Add assistant's tool use to messages
            assistant_content = []
            for b in content_blocks:
                if b.type == 'tool_use':
                    assistant_content.append({
                        'type': 'tool_use',
                        'id': b.id,
                        'name': b.name,
                        'input': b.input
                    })
                elif b.type == 'text':
                    assistant_content.append({
                        'type': 'text',
                        'text': b.text
                    })
            messages.append({'role': 'assistant', 'content': assistant_content})
            messages.append({'role': 'user', 'content': tool_results})
            
            # Next Claude call with tool results
            response = anthropic_client.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=1500,
                temperature=0.1,
                system=TRAFFIC_PROMPT,
                tools=CLAUDE_TOOLS,
                messages=messages
            )
        
        # If we hit max rounds and Claude still wants tools, force a final answer
        if tool_rounds >= max_tool_rounds and response.stop_reason == 'tool_use':
            print(f"[traffic] Hit max tool rounds ({max_tool_rounds}), forcing final answer")
            
            # Add Claude's last response to messages
            assistant_content = []
            for b in response.content:
                if b.type == 'tool_use':
                    assistant_content.append({
                        'type': 'tool_use',
                        'id': b.id,
                        'name': b.name,
                        'input': b.input
                    })
                elif b.type == 'text':
                    assistant_content.append({
                        'type': 'text',
                        'text': b.text
                    })
            messages.append({'role': 'assistant', 'content': assistant_content})
            
            # Tell Claude to wrap up with what it has
            messages.append({'role': 'user', 'content': "You've gathered enough information. Please provide your final JSON response now based on what you have."})
            
            # Final call WITHOUT tools to force JSON response
            response = anthropic_client.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=1500,
                temperature=0.1,
                system=TRAFFIC_PROMPT,
                messages=messages  # No tools parameter = must respond with text
            )
            print(f"[traffic] Forced final response, stop_reason: {response.stop_reason}")
        
        content_blocks = response.content
        
        # Extract text response
        result_text = ''
        for block in content_blocks:
            if block.type == 'text':
                result_text = block.text
                break
        
        result_text = strip_markdown_json(result_text)
        
        # If Claude returned text + JSON, extract just the JSON
        if result_text and not result_text.strip().startswith('{'):
            json_match = re.search(r'\{[\s\S]*\}', result_text)
            if json_match:
                print(f"[traffic] Extracting JSON from mixed response")
                result_text = json_match.group()
        
        routing = json.loads(result_text)
        
        # Debug logging - Claude's decision
        print(f"[traffic] === CLAUDE DECISION ===")
        print(f"[traffic] Type: {routing.get('type')}")
        print(f"[traffic] Route: {routing.get('route')}")
        print(f"[traffic] Confidence: {routing.get('confidence')}")
        print(f"[traffic] Client: {routing.get('clientCode')} / {routing.get('clientName')}")
        print(f"[traffic] Job: {routing.get('jobNumber')}")
        print(f"[traffic] Message: {routing.get('message', '')[:50]}...")
        print(f"[traffic] Reason: {routing.get('reason')}")
        
        # Update conversation memory for hub sessions
        if source == 'hub' and session_id:
            add_to_conversation(session_id, 'user', content)
            add_to_conversation(session_id, 'assistant', routing.get('message', '')[:200])
        
        return routing
        
    except json.JSONDecodeError as e:
        print(f"[traffic] Claude returned invalid JSON: {e}")
        print(f"[traffic] Raw response: {result_text if 'result_text' in dir() else 'No response'}")
        return {
            'type': 'error',
            'message': "Sorry, I got in a muddle over that one.",
            'confidence': 'low',
            'reason': 'Claude returned invalid JSON',
            'error': str(e)
        }
    
    except Exception as e:
        print(f"[traffic] Error calling Claude: {e}")
        import traceback
        traceback.print_exc()
        return {
            'type': 'error',
            'message': "Sorry, I got in a muddle over that one.",
            'confidence': 'low',
            'reason': 'Error calling Claude',
            'error': str(e)
        }
