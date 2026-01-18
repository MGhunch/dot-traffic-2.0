"""
Dot Traffic 2.0 - Connect
Route registry, email templates, downstream calls to workers and PA Postman
"""

import os
import httpx

# ===================
# CONFIG
# ===================

PA_POSTMAN_URL = os.environ.get('PA_POSTMAN_URL', '')

TIMEOUT = 30.0


# ===================
# ROUTE REGISTRY
# ===================

ROUTES = {
    "file": {
        "endpoint": "https://dot-file.up.railway.app/file",
        "status": "testing",  # live | testing | not_built
    },
    "update": {
        "endpoint": "https://dot-update.up.railway.app/process",
        "status": "not_built",
    },
    "triage": {
        "endpoint": "https://dot-triage.up.railway.app/process",
        "status": "not_built",
    },
    "incoming": {
        "endpoint": "https://dot-incoming.up.railway.app/process",
        "status": "not_built",
    },
    "wip": {
        "endpoint": "https://dot-wip.up.railway.app/process",
        "status": "not_built",
    },
    "todo": {
        "endpoint": "https://dot-todo.up.railway.app/process",
        "status": "not_built",
    },
    "tracker": {
        "endpoint": "https://dot-tracker.up.railway.app/process",
        "status": "not_built",
    },
    "work-to-client": {
        "endpoint": "https://dot-update.up.railway.app/process",
        "status": "not_built",
    },
    "feedback": {
        "endpoint": "https://dot-update.up.railway.app/process",
        "status": "not_built",
    },
    "clarify": {
        "endpoint": "PA_POSTMAN",
        "status": "testing",
    },
    "confirm": {
        "endpoint": "PA_POSTMAN",
        "status": "testing",
    },
}


# ===================
# EMAIL TEMPLATES
# ===================

EMAIL_TEMPLATES = {
    # We have one or more possible jobs - show cards
    "confirm": """
<p>Hi {sender_name},</p>
<p>I'm not totally sure which job you mean. Do any of these look right?</p>
{job_cards}
<p>Click a card to open it in Hub, or just reply with the job number and I'll get on with it.</p>
<p>Dot</p>
""",
    
    # Can't identify client or intent at all
    "no_idea": """
<p>Hi {sender_name},</p>
<p>Throw me a bone here - I've got totally no idea what you're asking for.</p>
<p>Come back to me with a job number, or a client - and I'll see what I can do.</p>
<p>Dot</p>
""",
    
    # Job number provided but doesn't exist
    "job_not_found": """
<p>Hi {sender_name},</p>
<p>I couldn't find job <strong>{job_number}</strong> in the system.</p>
<p>Please check the job number and try again, or reply <strong>TRIAGE</strong> if this is a new job.</p>
<p>Dot</p>
""",
}


def _format_job_cards(possible_jobs):
    """Format list of possible jobs as HTML cards with Hub links"""
    if not possible_jobs:
        return "<p><em>No active jobs found</em></p>"
    
    # Hub base URL
    HUB_URL = "https://dot-hub.up.railway.app"
    
    cards = []
    for job in possible_jobs[:5]:  # Max 5 jobs
        job_number = job.get('jobNumber', '')
        job_name = job.get('jobName', '')
        stage = job.get('stage', '')
        status = job.get('status', '')
        update_due = job.get('updateDue', 'TBC')
        with_client = job.get('withClient', False)
        
        # Build Hub link
        hub_link = f"{HUB_URL}/?job={job_number.replace(' ', '')}&action=edit"
        
        # Status badge
        status_text = "With client" if with_client else stage
        
        # Card HTML - inline styles for email compatibility
        card = f"""
<table cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-bottom:12px;">
  <tr>
    <td style="background:#f5f5f5; border-radius:8px; padding:16px; border-left:4px solid #ED1C24;">
      <a href="{hub_link}" style="text-decoration:none; color:inherit; display:block;">
        <table cellpadding="0" cellspacing="0" border="0" width="100%">
          <tr>
            <td style="font-size:16px; font-weight:600; color:#1a1a1a; padding-bottom:4px;">
              {job_number} | {job_name}
            </td>
          </tr>
          <tr>
            <td style="font-size:13px; color:#666;">
              {status_text} | Due {update_due}
            </td>
          </tr>
        </table>
      </a>
    </td>
  </tr>
</table>
"""
        cards.append(card)
    
    return "\n".join(cards)


def build_email(clarify_type, routing_data):
    """
    Build email HTML from template and routing data.
    
    Args:
        clarify_type: One of: confirm_job, unknown_job, no_idea, job_not_found
        routing_data: Dict with routing info from Claude
    
    Returns:
        HTML string for email body
    """
    # Map old clarify types to new simplified ones
    type_mapping = {
        'confirm_job': 'confirm',
        'unknown_job': 'confirm',
        'pick_job': 'confirm',
        'no_idea': 'no_idea',
        'job_not_found': 'job_not_found',
    }
    template_type = type_mapping.get(clarify_type, clarify_type)
    template = EMAIL_TEMPLATES.get(template_type, EMAIL_TEMPLATES['no_idea'])
    
    # Get sender name (default to "there")
    sender_name = routing_data.get('senderName', '') or 'there'
    
    # Build job cards if needed
    job_cards = ""
    if template_type == "confirm":
        possible_jobs = routing_data.get('possibleJobs', [])
        # If we have a single suggested job, wrap it in a list
        if not possible_jobs and routing_data.get('suggestedJob'):
            possible_jobs = [routing_data.get('suggestedJob')]
        # If we have jobNumber but no possibleJobs, create a minimal job object
        if not possible_jobs and routing_data.get('jobNumber'):
            possible_jobs = [{
                'jobNumber': routing_data.get('jobNumber', ''),
                'jobName': routing_data.get('jobName', ''),
                'stage': routing_data.get('currentStage', ''),
                'status': routing_data.get('currentStatus', ''),
                'updateDue': 'TBC',
                'withClient': routing_data.get('withClient', False),
            }]
        job_cards = _format_job_cards(possible_jobs)
    
    # Get job number for job_not_found template
    job_number = routing_data.get('jobNumber', '')
    
    # Format template
    html = template.format(
        sender_name=sender_name,
        client_name=routing_data.get('clientName', 'your client'),
        job_number=job_number,
        job_cards=job_cards
    )
    
    return html.strip()


# ===================
# DOWNSTREAM CALLS
# ===================

def call_worker(route, payload):
    """
    Call a downstream worker with the universal payload.
    
    Args:
        route: The route name (file, update, triage, etc.)
        payload: The universal payload dict
    
    Returns:
        dict with result info
    """
    route_config = ROUTES.get(route)
    
    if not route_config:
        return {
            'success': False,
            'error': f'Unknown route: {route}',
            'status': 'unknown'
        }
    
    status = route_config['status']
    endpoint = route_config['endpoint']
    
    # If not built, just return what we would have sent
    if status == 'not_built':
        return {
            'success': True,
            'status': 'not_built',
            'would_send_to': endpoint,
            'payload': payload,
            'message': f'Route "{route}" not built yet. Logged payload.'
        }
    
    # If testing, log but also try to call
    if status == 'testing':
        print(f"[connect] Testing route '{route}' -> {endpoint}")
    
    # Handle PA Postman (email sending)
    if endpoint == 'PA_POSTMAN':
        return call_postman(route, payload)
    
    # Call the worker
    try:
        response = httpx.post(
            endpoint,
            json=payload,
            timeout=TIMEOUT,
            headers={'Content-Type': 'application/json'}
        )
        
        return {
            'success': response.status_code == 200,
            'status': status,
            'endpoint': endpoint,
            'response_code': response.status_code,
            'response': response.json() if response.status_code == 200 else response.text
        }
        
    except Exception as e:
        print(f"[connect] Error calling {endpoint}: {e}")
        return {
            'success': False,
            'status': status,
            'endpoint': endpoint,
            'error': str(e)
        }


def call_postman(route, payload):
    """
    Call PA Postman to send an email (for clarify/confirm routes).
    
    Args:
        route: Either 'clarify' or 'confirm'
        payload: The universal payload dict
    
    Returns:
        dict with result info
    """
    # Build the postman payload - matches PA schema: to, subject, body
    postman_payload = {
        'to': payload.get('senderEmail', ''),
        'subject': f"Re: {payload.get('subjectLine', '')}",
        'body': payload.get('emailHtml', '')
    }
    
    if not PA_POSTMAN_URL:
        return {
            'success': False,
            'status': 'testing',
            'error': 'PA_POSTMAN_URL not configured',
            'would_send': postman_payload
        }
    
    try:
        response = httpx.post(
            PA_POSTMAN_URL,
            json=postman_payload,
            timeout=TIMEOUT,
            headers={'Content-Type': 'application/json'}
        )
        
        return {
            'success': response.status_code == 200 or response.status_code == 202,
            'status': 'live',
            'endpoint': 'PA_POSTMAN',
            'response_code': response.status_code
        }
        
    except Exception as e:
        print(f"[connect] Error calling PA Postman: {e}")
        return {
            'success': False,
            'status': 'testing',
            'endpoint': 'PA_POSTMAN',
            'error': str(e),
            'would_send': postman_payload
        }
