"""
Dot Traffic 2.0 - Connect
Route registry, email templates, downstream calls to workers and PA Postman

Updated: Added salutations, fixed logo ratio, new copy, send_answer(), send_redirect(), send_not_built()
"""

import os
import httpx

# ===================
# CONFIG
# ===================

PA_POSTMAN_URL = os.environ.get('PA_POSTMAN_URL', '')

TIMEOUT = 30.0

# Logo for email footer (300x150 original, display at 56x28 to maintain 2:1 ratio)
LOGO_URL = "https://raw.githubusercontent.com/MGhunch/dot-hub/main/images/ai2-logo.png"

# Hub base URL
HUB_URL = "https://dot.hunch.co.nz"


# ===================
# ROUTE REGISTRY
# ===================

ROUTES = {
    "file": {
        "endpoint": "https://dot-file.up.railway.app/file",
        "status": "testing",  # live | testing | not_built
        "friendly_name": "File",
    },
    "update": {
        "endpoint": "https://dot-update.up.railway.app/process",
        "status": "not_built",
        "friendly_name": "Update",
    },
    "triage": {
        "endpoint": "https://dot-triage.up.railway.app/process",
        "status": "not_built",
        "friendly_name": "Triage",
    },
    "new-job": {
        "endpoint": "https://dot-new-job.up.railway.app/process",
        "status": "not_built",
        "friendly_name": "New Job",
    },
    "wip": {
        "endpoint": "https://dot-wip.up.railway.app/process",
        "status": "not_built",
        "friendly_name": "WIP",
    },
    "todo": {
        "endpoint": "https://dot-todo.up.railway.app/process",
        "status": "not_built",
        "friendly_name": "To-do",
    },
    "tracker": {
        "endpoint": "https://dot-tracker.up.railway.app/process",
        "status": "not_built",
        "friendly_name": "Tracker",
    },
    "work-to-client": {
        "endpoint": "https://dot-update.up.railway.app/process",
        "status": "not_built",
        "friendly_name": "Work to Client",
    },
    "feedback": {
        "endpoint": "https://dot-update.up.railway.app/process",
        "status": "not_built",
        "friendly_name": "Feedback",
    },
    "clarify": {
        "endpoint": "PA_POSTMAN",
        "status": "testing",
        "friendly_name": "Clarify",
    },
    "confirm": {
        "endpoint": "PA_POSTMAN",
        "status": "testing",
        "friendly_name": "Confirm",
    },
}


# ===================
# HELPER: Extract first name from sender
# ===================

def _get_first_name(sender_name):
    """Extract first name from sender name, fallback to 'there'"""
    if not sender_name:
        return "there"
    # Take first word, strip any quotes or brackets
    first = sender_name.split()[0].strip('"\'[]()') if sender_name else "there"
    return first if first else "there"


# ===================
# EMAIL WRAPPER & FOOTER
# ===================

def _email_wrapper(content):
    """Wrap email content with consistent styling and footer"""
    return f"""<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 15px; line-height: 1.6; color: #333;">
{content}

<table cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-top: 32px; border-top: 1px solid #eee; padding-top: 16px;">
  <tr>
    <td style="vertical-align: middle; padding-right: 12px;" width="60">
      <img src="{LOGO_URL}" alt="hai2" width="56" height="28" style="display: block;">
    </td>
    <td style="vertical-align: middle; font-size: 12px; color: #999;">
      Dot is a robot, but there's humans in the loop.
    </td>
  </tr>
</table>
</div>"""


def _success_box(title, subtitle):
    """Green success detail box with tick"""
    return f"""<table cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-bottom: 20px;">
  <tr>
    <td style="background: #f0fdf4; border-radius: 8px; padding: 16px; border-left: 4px solid #22c55e;">
      <table cellpadding="0" cellspacing="0" border="0" width="100%">
        <tr>
          <td width="28" style="vertical-align: top; padding-right: 12px;">
            <div style="width: 24px; height: 24px; background: #22c55e; border-radius: 50%; text-align: center; line-height: 24px;">
              <span style="color: white; font-size: 14px;">âœ“</span>
            </div>
          </td>
          <td style="vertical-align: top;">
            <div style="font-weight: 600; color: #333; margin-bottom: 2px;">{title}</div>
            <div style="font-size: 13px; color: #666;">{subtitle}</div>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>"""


def _failure_box(title, subtitle):
    """Red failure detail box with X"""
    return f"""<table cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-bottom: 20px;">
  <tr>
    <td style="background: #fef2f2; border-radius: 8px; padding: 16px; border-left: 4px solid #ef4444;">
      <table cellpadding="0" cellspacing="0" border="0" width="100%">
        <tr>
          <td width="28" style="vertical-align: top; padding-right: 12px;">
            <div style="width: 24px; height: 24px; background: #ef4444; border-radius: 50%; text-align: center; line-height: 24px;">
              <span style="color: white; font-size: 14px;">âœ•</span>
            </div>
          </td>
          <td style="vertical-align: top;">
            <div style="font-weight: 600; color: #333; margin-bottom: 2px;">{title}</div>
            <div style="font-size: 13px; color: #666;">{subtitle}</div>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>"""


# ===================
# EMAIL TEMPLATES (for clarify/confirm)
# ===================

EMAIL_TEMPLATES = {
    # We have one or more possible jobs - show cards
    "confirm": """<p style="margin: 0 0 20px 0;">Hey {first_name},</p>
<p style="margin: 0 0 20px 0;">I'm not totally sure which job you mean. Do any of these look right?</p>
{job_cards}
<p style="margin: 0 0 24px 0;">Just reply with a job number and I'll get on with it.</p>
<p style="margin: 0;">Dot</p>
""",
    
    # Can't identify client or intent at all
    "no_idea": """<p style="margin: 0 0 20px 0;">Hey {first_name},</p>
<p style="margin: 0 0 20px 0;">Throw me a bone, I have no idea what you're after.</p>
<p style="margin: 0 0 24px 0;">Let me know which client or project... bonus points for a job number.</p>
<p style="margin: 0;">Dot</p>
""",
    
    # Job number provided but doesn't exist
    "job_not_found": """<p style="margin: 0 0 20px 0;">Hey {first_name},</p>
<p style="margin: 0 0 20px 0;">Sorry, I can't find job <strong>{job_number}</strong> right now.</p>
<p style="margin: 0 0 24px 0;">Please check the job number and try again, or reply "Incoming" if it's a new job.</p>
<p style="margin: 0;">Dot</p>
""",
}


def _format_job_cards(possible_jobs):
    """Format list of possible jobs as HTML cards with Hub links"""
    if not possible_jobs:
        return "<p><em>No active jobs found</em></p>"
    
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
    
    # Get first name for salutation
    first_name = _get_first_name(routing_data.get('senderName', ''))
    
    # Get job number for job_not_found template
    job_number = routing_data.get('jobNumber', '')
    
    # Format template
    content = template.format(
        first_name=first_name,
        job_number=job_number,
        job_cards=job_cards
    )
    
    # Wrap with styling and footer
    return _email_wrapper(content)


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
    
    # If not built, send "not built" email and return
    if status == 'not_built':
        # Send not_built email to user
        sender_email = payload.get('senderEmail', '')
        sender_name = payload.get('senderName', '')
        subject_line = payload.get('subjectLine', '')
        
        if sender_email:
            send_not_built(
                to_email=sender_email,
                route=route,
                sender_name=sender_name,
                subject_line=subject_line
            )
        
        return {
            'success': True,
            'status': 'not_built',
            'would_send_to': endpoint,
            'payload': payload,
            'message': f'Route "{route}" not built yet. User notified.'
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


# ===================
# CONFIRMATION EMAILS
# ===================

ROUTE_FRIENDLY_TEXT = {
    'file': 'Files filed',
    'update': 'Job updated',
    'triage': 'Job triaged',
    'new-job': 'New job logged',
    'feedback': 'Feedback logged',
    'work-to-client': 'Work sent to client logged',
}

ROUTE_SUBTITLE = {
    'file': 'Filed to {destination}',
    'update': 'Status updated',
    'triage': 'New job created',
    'new-job': 'Added to pipeline',
    'feedback': 'Feedback recorded',
    'work-to-client': 'Delivery logged',
}

# Routes that don't need confirmation (they already send emails)
NO_CONFIRM_ROUTES = ['clarify', 'confirm', 'wip', 'todo', 'tracker', 'answer', 'redirect']


def send_confirmation(to_email, route, sender_name=None, client_name=None, job_number=None, job_name=None,
                      subject_line=None, files_url=None, destination=None, original_email=None):
    """
    Send a confirmation email after successful worker action.
    
    Args:
        to_email: Recipient email
        route: The route that was executed
        sender_name: Sender's name for salutation
        client_name: Client name (optional)
        job_number: Job number (optional)
        job_name: Job name (optional)
        subject_line: Original email subject for Re: line
        files_url: SharePoint folder URL (optional)
        destination: Where files were filed (optional)
        original_email: Original email data for trail (optional)
    
    Returns:
        dict with result info
    """
    if route in NO_CONFIRM_ROUTES:
        return {'success': True, 'skipped': True, 'reason': 'Route sends its own email'}
    
    first_name = _get_first_name(sender_name)
    friendly_text = ROUTE_FRIENDLY_TEXT.get(route, 'Request completed')
    
    # Build title line: "ONE 066 | Email Design System" or just "ONE 066"
    if job_number and job_name:
        box_title = f"{job_number} | {job_name}"
    elif job_number:
        box_title = job_number
    elif client_name:
        box_title = client_name
    else:
        box_title = "Done"
    
    # Build subtitle
    subtitle_template = ROUTE_SUBTITLE.get(route, 'Completed')
    box_subtitle = subtitle_template.format(destination=destination or 'job folder')
    
    # Build files link if available
    files_link = ''
    if files_url:
        files_link = f'<p style="margin: 0 0 24px 0;"><a href="{files_url}" style="color: #ED1C24; text-decoration: none; font-weight: 500;">See the files â†’</a></p>'
    
    # Build email content
    content = f"""<p style="margin: 0 0 20px 0;">Hey {first_name},</p>
<p style="margin: 0 0 20px 0;">All sorted. {friendly_text}.</p>

{_success_box(box_title, box_subtitle)}

{files_link}
<p style="margin: 0;">Dot</p>"""
    
    body_html = _email_wrapper(content)
    
    subject = f"Re: {subject_line}" if subject_line else "Dot - Done"
    
    postman_payload = {
        'to': to_email,
        'subject': subject,
        'body': body_html
    }
    
    print(f"[connect] Sending confirmation: {friendly_text} -> {to_email}")
    
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
        print(f"[connect] Error sending confirmation: {e}")
        return {
            'success': False,
            'status': 'testing',
            'endpoint': 'PA_POSTMAN',
            'error': str(e),
            'would_send': postman_payload
        }


# ===================
# FAILURE EMAILS
# ===================

def send_failure(to_email, route, error_message, sender_name=None, subject_line=None, job_number=None,
                 job_name=None, client_name=None, original_email=None):
    """
    Send a failure notification email when a worker fails.
    
    Args:
        to_email: Recipient email
        route: The route that failed
        error_message: The error message from the worker
        sender_name: Sender's name for salutation
        subject_line: Original email subject
        job_number: Job number (optional)
        job_name: Job name (optional)
        client_name: Client name (optional)
        original_email: Original email data for trail (optional)
    
    Returns:
        dict with result info
    """
    if route in NO_CONFIRM_ROUTES:
        return {'success': True, 'skipped': True, 'reason': 'Route sends its own email'}
    
    first_name = _get_first_name(sender_name)
    
    # Build title line
    if job_number and job_name:
        box_title = f"{job_number} | {job_name}"
    elif job_number:
        box_title = job_number
    elif client_name:
        box_title = client_name
    else:
        box_title = "Error"
    
    # Build subtitle based on route
    route_action = {
        'file': "Couldn't file attachments",
        'update': "Couldn't update job",
        'triage': "Couldn't create job",
        'new-job': "Couldn't log new job",
        'feedback': "Couldn't log feedback",
        'work-to-client': "Couldn't log delivery",
    }
    box_subtitle = route_action.get(route, "Something went wrong")
    
    # Build email content
    content = f"""<p style="margin: 0 0 20px 0;">Hey {first_name},</p>
<p style="margin: 0 0 20px 0;">Sorry, I got in a muddle over that one.</p>

{_failure_box(box_title, box_subtitle)}

<p style="margin: 0 0 8px 0; font-size: 13px; color: #666;">Here's what I told myself in Dot Language:</p>
<pre style="background: #f5f5f5; padding: 12px; border-radius: 6px; font-size: 12px; overflow-x: auto; color: #666; margin: 0 0 24px 0; font-family: 'SF Mono', Monaco, 'Courier New', monospace;">{error_message}</pre>

<p style="margin: 0;">Dot</p>"""
    
    body_html = _email_wrapper(content)
    
    subject = f"Did not compute: {subject_line}" if subject_line else "Did not compute"
    
    postman_payload = {
        'to': to_email,
        'subject': subject,
        'body': body_html
    }
    
    print(f"[connect] Sending failure notification: {route} failed -> {to_email}")
    
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
        print(f"[connect] Error sending failure notification: {e}")
        return {
            'success': False,
            'status': 'testing',
            'endpoint': 'PA_POSTMAN',
            'error': str(e),
            'would_send': postman_payload
        }


# ===================
# ANSWER EMAILS (Q&A responses)
# ===================

def send_answer(to_email, message, sender_name=None, subject_line=None, 
                client_code=None, client_name=None, original_email=None):
    """
    Send an answer email - Dot's response to a question.
    
    Args:
        to_email: Recipient email
        message: Dot's answer message
        sender_name: Sender's name for salutation
        subject_line: Original email subject for Re: line
        client_code: Client code (optional)
        client_name: Client name (optional)
        original_email: Original email data for trail (optional)
    
    Returns:
        dict with result info
    """
    first_name = _get_first_name(sender_name)
    
    # Build email content - simple message
    content = f"""<p style="margin: 0 0 20px 0;">Hey {first_name},</p>
<p style="margin: 0 0 20px 0;">{message}</p>
<p style="margin: 0;">Dot</p>"""
    
    body_html = _email_wrapper(content)
    
    subject = f"Re: {subject_line}" if subject_line else "Dot"
    
    postman_payload = {
        'to': to_email,
        'subject': subject,
        'body': body_html
    }
    
    print(f"[connect] Sending answer -> {to_email}")
    
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
        print(f"[connect] Error sending answer: {e}")
        return {
            'success': False,
            'status': 'testing',
            'endpoint': 'PA_POSTMAN',
            'error': str(e),
            'would_send': postman_payload
        }


# ===================
# REDIRECT EMAILS (WIP/Tracker)
# ===================

def send_redirect(to_email, message, sender_name=None, subject_line=None,
                  client_code=None, client_name=None, redirect_to='wip', original_email=None):
    """
    Send a redirect email - pointing user to WIP or Tracker.
    
    Args:
        to_email: Recipient email
        message: Dot's redirect message (optional - will use default if empty)
        sender_name: Sender's name for salutation
        subject_line: Original email subject for Re: line
        client_code: Client code for link
        client_name: Client name for display
        redirect_to: Where to redirect - 'wip' or 'tracker'
        original_email: Original email data for trail (optional)
    
    Returns:
        dict with result info
    """
    first_name = _get_first_name(sender_name)
    redirect_to_lower = (redirect_to or 'wip').lower()
    
    # Build the Hub link
    client_param = f"?client={client_code}" if client_code else ""
    view_param = f"&view={redirect_to_lower}" if client_param else f"?view={redirect_to_lower}"
    hub_link = f"{HUB_URL}/{client_param}{view_param}"
    
    # Display name for client
    display_name = client_name or client_code or ""
    
    # Choose message based on redirect type
    if redirect_to_lower == 'tracker':
        default_message = "Gosh, that's getting into more detail than I'm good at. You should find everything you need in the Tracker."
        link_text = f"Open Tracker for {display_name} â†’" if display_name else "Open Tracker â†’"
    else:
        default_message = "That's getting into the detail more than I'm good at. You should find everything you need in the WIP."
        link_text = f"Open {display_name} WIP â†’" if display_name else "Open WIP â†’"
    
    # Use provided message or default
    display_message = message if message else default_message
    
    # Build email content
    content = f"""<p style="margin: 0 0 20px 0;">Hey {first_name},</p>
<p style="margin: 0 0 20px 0;">{display_message}</p>
<p style="margin: 0 0 24px 0;"><a href="{hub_link}" style="color: #ED1C24; text-decoration: none; font-weight: 500;">{link_text}</a></p>
<p style="margin: 0;">Dot</p>"""
    
    body_html = _email_wrapper(content)
    
    subject = f"Re: {subject_line}" if subject_line else "Dot"
    
    postman_payload = {
        'to': to_email,
        'subject': subject,
        'body': body_html
    }
    
    print(f"[connect] Sending redirect ({redirect_to_lower}) -> {to_email}")
    
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
        print(f"[connect] Error sending redirect: {e}")
        return {
            'success': False,
            'status': 'testing',
            'endpoint': 'PA_POSTMAN',
            'error': str(e),
            'would_send': postman_payload
        }


# ===================
# NOT BUILT EMAILS
# ===================

def send_not_built(to_email, route, sender_name=None, subject_line=None):
    """
    Send a "not built yet" email when user tries to use an action that isn't ready.
    
    Args:
        to_email: Recipient email
        route: The route that isn't built yet
        sender_name: Sender's name for salutation
        subject_line: Original email subject for Re: line
    
    Returns:
        dict with result info
    """
    first_name = _get_first_name(sender_name)
    
    # Route-specific messages
    route_messages = {
        'update': f"Still working on updates. You can update jobs in the Hub for now. <a href=\"{HUB_URL}\" style=\"color: #ED1C24;\">Open Hub →</a>",
        'triage': "Triage isn't ready yet. Watch this space.",
        'todo': f"To-do lists coming soon. Check the WIP in the Hub for now. <a href=\"{HUB_URL}/?view=wip\" style=\"color: #ED1C24;\">Open WIP →</a>",
        'new-job': "Not set up for new jobs yet. Better to email a human.",
    }
    
    # Get route-specific message or generic fallback
    route_config = ROUTES.get(route, {})
    friendly_name = route_config.get('friendly_name', route.title())
    message = route_messages.get(route, f"Sorry, we're still working on <strong>{friendly_name}</strong>. Hoping to have it up and running soon.")
    
    # Build email content
    content = f"""<p style="margin: 0 0 20px 0;">Hey {first_name},</p>
<p style="margin: 0 0 20px 0;">{message}</p>
<p style="margin: 0;">Dot</p>"""
    
    body_html = _email_wrapper(content)
    
    subject = f"Re: {subject_line}" if subject_line else "Dot - Coming Soon"
    
    postman_payload = {
        'to': to_email,
        'subject': subject,
        'body': body_html
    }
    
    print(f"[connect] Sending not_built notification: {route} -> {to_email}")
    
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
        print(f"[connect] Error sending not_built notification: {e}")
        return {
            'success': False,
            'status': 'testing',
            'endpoint': 'PA_POSTMAN',
            'error': str(e),
            'would_send': postman_payload
        }
