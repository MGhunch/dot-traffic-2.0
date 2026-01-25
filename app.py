"""
Dot Traffic 2.0 (Brain)
Intelligent routing layer for Hunch's agency workflow.
Receives emails from PA Listener or Hub, routes to workers.

ARCHITECTURE:
- BRAIN THINKS (traffic.py) - Claude decides what to do
- WORKERS WORK (dot-workers) - Do the actual work + communicate results
- AIRTABLE REMEMBERS (airtable.py) - Data persistence
- CONNECT COMMUNICATES (connect.py) - Email (for answers/redirects/clarify)

FLOW:
1. Gates (ignore self, check sender domain, deduplication)
2. Check for pending clarify reply
3. Call Claude (Claude uses tools to fetch jobs, make decisions)
4. Log to Traffic table
5. Route based on type:
   - answer/redirect/clarify → connect.py sends email directly
   - action → call worker, worker handles everything (file, Teams, confirmation)
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import httpx
import airtable
import traffic
import connect

app = Flask(__name__)
CORS(app)

# ===================
# WORKER URLS
# ===================

WORKER_URLS = {
    'update': 'https://dot-workers.up.railway.app/update',
    # Future workers:
    # 'newjob': 'https://dot-workers.up.railway.app/newjob',
    # 'triage': 'https://dot-workers.up.railway.app/triage',
}

WORKER_TIMEOUT = 60.0  # Workers do more now, give them time


def call_worker(route, payload):
    """
    Call a worker service.
    Workers handle everything: file attachments, Airtable updates, Teams, confirmation emails.
    
    Returns dict with success status and worker response.
    """
    url = WORKER_URLS.get(route)
    
    if not url:
        print(f"[app] No worker URL configured for route: {route}")
        return {
            'success': False,
            'error': f'No worker configured for route: {route}',
            'route': route
        }
    
    print(f"[app] Calling worker: {route} -> {url}")
    
    try:
        response = httpx.post(
            url,
            json=payload,
            timeout=WORKER_TIMEOUT,
            headers={'Content-Type': 'application/json'}
        )
        
        success = response.status_code == 200
        
        try:
            response_data = response.json()
        except:
            response_data = response.text
        
        print(f"[app] Worker response: {response.status_code}, success={success}")
        
        return {
            'success': success,
            'status_code': response.status_code,
            'response': response_data
        }
        
    except httpx.TimeoutException:
        print(f"[app] Worker timeout: {route}")
        return {
            'success': False,
            'error': f'Worker timeout after {WORKER_TIMEOUT}s',
            'route': route
        }
    except Exception as e:
        print(f"[app] Worker error: {route} - {e}")
        return {
            'success': False,
            'error': str(e),
            'route': route
        }


# ===================
# HEALTH CHECK
# ===================

@app.route('/', methods=['GET'])
@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'Dot Brain',
        'version': '3.1',
        'architecture': 'brain-thinks-workers-work',
        'workers': list(WORKER_URLS.keys())
    })


# ===================
# SESSION CLEAR (Hub)
# ===================

@app.route('/traffic/clear', methods=['POST'])
def clear_session():
    """Clear conversation memory for a Hub session"""
    try:
        data = request.get_json()
        session_id = data.get('sessionId')
        if session_id:
            traffic.clear_conversation(session_id)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ===================
# HUB ENDPOINT (Simple Claude - Fast)
# ===================

@app.route('/hub', methods=['POST'])
def handle_hub():
    """
    Fast path for Hub requests.
    Simple Claude - no tools, jobs in context.
    ~2-3 seconds vs ~8 seconds for full traffic.
    """
    try:
        import hub
        data = request.get_json()
        
        # Validate
        content = data.get('content', '')
        if not content:
            return jsonify({'error': 'No content provided'}), 400
        
        # Simple Claude handles it
        result = hub.handle_hub_request(data)
        
        return jsonify(result)
        
    except Exception as e:
        print(f"[app] Hub error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'type': 'answer',
            'message': "Sorry, I got in a muddle over that one.",
            'jobs': None
        }), 500


# ===================
# MAIN TRAFFIC ENDPOINT (Full Claude - Email)
# ===================

@app.route('/traffic', methods=['POST'])
def handle_traffic():
    """
    Main routing endpoint. Receives requests from PA Listener (email) or Hub.
    Claude uses tools to identify client, fetch jobs, and decide.
    """
    try:
        data = request.get_json()
        
        # ===================
        # VALIDATE INPUT
        # ===================
        content = data.get('content') or data.get('body') or data.get('emailContent', '')
        subject = data.get('subject') or data.get('subjectLine', '')
        
        if not content and subject:
            content = f"Subject: {subject}"
        
        if not content:
            return jsonify({'error': 'No email body or subject provided'}), 400
        
        sender_email = data.get('from') or data.get('senderEmail', '')
        sender_name = data.get('senderName', '')
        has_attachments = data.get('hasAttachments', False)
        source = data.get('source', 'email')
        internet_message_id = data.get('internetMessageId', '')
        conversation_id = data.get('conversationId', '')
        received_datetime = data.get('receivedDateTime', '')
        
        # ===================
        # STEP 1: IGNORE DOT'S OWN EMAILS
        # ===================
        if sender_email.lower() == 'dot@hunch.co.nz':
            return jsonify({
                'route': 'ignored',
                'status': 'self',
                'reason': 'Ignoring email from Dot to prevent loops'
            })
        
        # ===================
        # STEP 2: CHECK SENDER DOMAIN
        # ===================
        if not sender_email.lower().endswith('@hunch.co.nz'):
            airtable.log_traffic(
                internet_message_id, conversation_id, 'external', 'ignored',
                None, None, sender_email, subject
            )
            return jsonify({
                'route': 'external',
                'status': 'ignored',
                'reason': 'External sender - only @hunch.co.nz emails are processed',
                'senderEmail': sender_email
            })
        
        # ===================
        # STEP 3: DEDUPLICATION
        # ===================
        if internet_message_id:
            existing = airtable.check_duplicate(internet_message_id)
            if existing:
                return jsonify({
                    'route': 'duplicate',
                    'status': 'already_processed',
                    'reason': 'Email already processed',
                    'originalRoute': existing['fields'].get('Route', ''),
                    'originalRecordId': existing['id']
                })
        
        # ===================
        # STEP 4: CHECK PENDING CLARIFY
        # ===================
        if conversation_id:
            pending_clarify = airtable.check_pending_clarify(conversation_id)
            if pending_clarify:
                result = handle_clarify_reply(data, pending_clarify)
                if result:
                    return jsonify(result)
        
        # ===================
        # STEP 5: CALL CLAUDE
        # ===================
        print(f"[app] === ROUTING ===")
        print(f"[app] Source: {source}")
        print(f"[app] Subject: {subject}")
        print(f"[app] Sender: {sender_email}")
        
        routing = traffic.route_request(data)
        
        print(f"[app] Type: {routing.get('type')}")
        print(f"[app] Route: {routing.get('route')}")
        print(f"[app] Client: {routing.get('clientCode')}")
        print(f"[app] Job: {routing.get('jobNumber')}")
        
        if routing.get('type') == 'error':
            return jsonify(routing), 500
        
        # ===================
        # STEP 5b: ENRICH WITH PROJECT DATA
        # ===================
        if routing.get('jobNumber'):
            project = airtable.get_project(routing.get('jobNumber'))
            if project:
                routing = enrich_with_project(routing, project)
                print(f"[app] Enriched: teamId={routing.get('teamId')}, channelId={routing.get('teamsChannelId')}")
        
        # ===================
        # STEP 6: LOG TO TRAFFIC TABLE
        # ===================
        response_type = routing.get('type', 'action')
        route = routing.get('route', 'unknown')
        
        log_route = response_type if response_type in ['clarify', 'confirm', 'answer', 'redirect'] else route
        status = 'pending' if response_type in ['clarify', 'confirm'] else 'processed'
        
        airtable.log_traffic(
            internet_message_id, conversation_id, log_route, status,
            routing.get('jobNumber'), routing.get('clientCode'),
            sender_email, subject, content  # Pass email body for storage
        )
        
        # ===================
        # STEP 7: BUILD PAYLOAD
        # ===================
        payload = build_worker_payload(data, routing)
        
        # ===================
        # STEP 8: ROUTE BASED ON TYPE
        # ===================
        worker_result = None
        
        # Build original email for trail (used by connect.py)
        original_email = {
            'senderName': sender_name,
            'senderEmail': sender_email,
            'subject': subject,
            'receivedDateTime': received_datetime,
            'content': content
        }
        
        if response_type == 'answer':
            # ANSWER: Brain sends email directly via connect.py
            if source == 'email':
                worker_result = connect.send_answer(
                    to_email=sender_email,
                    message=routing.get('message', ''),
                    sender_name=sender_name,
                    subject_line=subject,
                    original_email=original_email
                )
            else:
                worker_result = {'success': True, 'status': 'answered'}
                
        elif response_type == 'redirect':
            # REDIRECT: Brain sends email directly via connect.py
            if source == 'email':
                worker_result = connect.send_redirect(
                    to_email=sender_email,
                    sender_name=sender_name,
                    subject_line=subject,
                    client_code=routing.get('clientCode'),
                    client_name=routing.get('clientName'),
                    redirect_to=routing.get('redirectTo', 'wip'),
                    message=routing.get('message'),
                    original_email=original_email
                )
            else:
                worker_result = {'success': True, 'status': 'redirected'}
                
        elif response_type in ['clarify', 'confirm']:
            # CLARIFY/CONFIRM: Brain sends email directly via connect.py
            if source == 'email':
                clarify_type = routing.get('clarifyType', 'no_idea')
                if response_type == 'confirm':
                    clarify_type = 'confirm'
                
                worker_result = connect.send_clarify(
                    to_email=sender_email,
                    clarify_type=clarify_type,
                    sender_name=sender_name,
                    subject_line=subject,
                    job_number=routing.get('jobNumber'),
                    possible_jobs=routing.get('jobs') or routing.get('possibleJobs'),
                    original_email=original_email
                )
            else:
                worker_result = {'success': True, 'status': 'pending_user_input'}
                
        elif response_type == 'action':
            # ACTION: Call worker - worker handles EVERYTHING
            # (file attachments, Airtable updates, Teams post, confirmation email)
            if source == 'email':
                worker_result = call_worker(route, payload)
                    
                # If worker failed, send failure email from Brain
                # (because worker might not have been able to send it)
                if not worker_result.get('success'):
                    connect.send_failure(
                        to_email=sender_email,
                        route=route,
                        error_message=worker_result.get('error', 'Unknown error'),
                        sender_name=sender_name,
                        subject_line=subject,
                        job_number=routing.get('jobNumber'),
                        job_name=routing.get('jobName'),
                        client_name=routing.get('clientName'),
                        original_email=original_email
                    )
            else:
                # Hub - return for user to act on
                worker_result = {'success': True, 'status': 'user_action_required'}
        else:
            # Unknown type
            worker_result = {'success': False, 'error': f'Unknown type: {response_type}'}
        
        # ===================
        # RETURN RESPONSE
        # ===================
        return jsonify({
            'type': response_type,
            'route': route,
            'confidence': routing.get('confidence', 'unknown'),
            'reason': routing.get('reason', ''),
            'message': routing.get('message', ''),
            'jobNumber': routing.get('jobNumber'),
            'clientCode': routing.get('clientCode'),
            'clientName': routing.get('clientName'),
            'intent': routing.get('intent'),
            'jobs': routing.get('jobs') or routing.get('possibleJobs'),
            'clarifyType': routing.get('clarifyType'),
            'redirectTo': routing.get('redirectTo'),
            'worker': worker_result
        })
        
    except Exception as e:
        print(f"[app] Error in /traffic: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'error': 'Internal server error',
            'details': str(e)
        }), 500


# ===================
# HELPER: ENRICH WITH PROJECT DATA
# ===================

def enrich_with_project(routing, project):
    """Add project data to routing dict"""
    routing['jobName'] = project['jobName']
    routing['clientName'] = project['clientName']
    routing['projectRecordId'] = project['recordId']
    routing['teamsChannelId'] = project['teamsChannelId']
    routing['teamId'] = project['teamId']
    routing['currentStage'] = project['stage']
    routing['currentStatus'] = project['status']
    routing['withClient'] = project['withClient']
    routing['filesUrl'] = project.get('filesUrl', '')
    return routing


# ===================
# CLARIFY REPLY HANDLER
# ===================

def handle_clarify_reply(data, pending_clarify):
    """
    Handle a reply to a previous clarify request.
    Returns routing dict if handled, None to continue normal processing.
    """
    content = (data.get('body') or data.get('emailContent', '')).strip()
    subject = data.get('subject') or data.get('subjectLine', '')
    sender_email = data.get('from') or data.get('senderEmail', '')
    sender_name = data.get('senderName', '')
    internet_message_id = data.get('internetMessageId', '')
    conversation_id = data.get('conversationId', '')
    received_datetime = data.get('receivedDateTime', '')
    has_attachments = data.get('hasAttachments', False)
    
    pending_fields = pending_clarify['fields']
    
    # Build original email for trail
    original_email = {
        'senderName': sender_name,
        'senderEmail': sender_email,
        'subject': subject,
        'receivedDateTime': received_datetime,
        'content': content
    }
    
    # Check for job number in reply
    reply_job_number = traffic.extract_job_number(subject)
    if not reply_job_number:
        reply_job_number = traffic.extract_job_number(content)
    
    # Check for YES confirmation
    content_upper = content.upper()
    affirmatives = [
        'YES', 'YES.', 'YES!', 'YEP', 'YUP', 'YEAH',
        'CONFIRM', 'CONFIRMED', 'CORRECT', "THAT'S RIGHT",
        'THATS RIGHT', "THAT'S THE ONE", 'THATS THE ONE',
        "THAT'S IT", 'THATS IT', 'BINGO', 'SPOT ON', 'PERFECT'
    ]
    is_yes = content_upper in affirmatives or content_upper.startswith('YES')
    
    # Check for TRIAGE request
    is_triage = content_upper in ['TRIAGE', 'TRIAGE.', 'NEW JOB', 'NEW']
    
    if is_triage:
        # User wants to triage as new job
        airtable.log_traffic(
            internet_message_id, conversation_id, 'triage', 'processed',
            None, None, sender_email, subject
        )
        airtable.update_traffic_record(pending_clarify['id'], {'Status': 'resolved'})
        
        # TODO: Call triage worker when built
        return {
            'route': 'triage',
            'confidence': 'high',
            'reason': 'User requested triage - worker not yet built',
            'worker': {'success': False, 'error': 'Triage worker not yet built'}
        }
    
    elif reply_job_number:
        # User provided a job number - validate it
        project = airtable.get_project(reply_job_number)
        
        if project:
            airtable.log_traffic(
                internet_message_id, conversation_id, 'update', 'processed',
                reply_job_number, reply_job_number.split()[0], sender_email, subject
            )
            airtable.update_traffic_record(pending_clarify['id'], {
                'Status': 'resolved',
                'JobNumber': reply_job_number
            })
            
            routing = {
                'route': 'update',
                'confidence': 'high',
                'jobNumber': reply_job_number,
                'reason': 'Job number provided in clarify reply'
            }
            routing = enrich_with_project(routing, project)
            routing['clientCode'] = reply_job_number.split()[0]
            
            payload = build_worker_payload(data, routing)
            
            # Call worker - worker handles file + update + comms
            worker_result = call_worker('update', payload)
            
            # If worker failed, send failure email
            if not worker_result.get('success'):
                connect.send_failure(
                    to_email=sender_email,
                    route='update',
                    error_message=worker_result.get('error', 'Unknown error'),
                    sender_name=sender_name,
                    subject_line=subject,
                    job_number=reply_job_number,
                    job_name=routing.get('jobName'),
                    client_name=routing.get('clientName'),
                    original_email=original_email
                )
            
            return {
                'route': 'update',
                'confidence': 'high',
                'reason': 'Job number provided in clarify reply',
                'jobNumber': reply_job_number,
                'worker': worker_result
            }
        else:
            # Invalid job number - clarify again
            connect.send_clarify(
                to_email=sender_email,
                clarify_type='job_not_found',
                sender_name=sender_name,
                subject_line=subject,
                job_number=reply_job_number,
                original_email=original_email
            )
            
            return {
                'route': 'clarify',
                'confidence': 'low',
                'reason': f"Job {reply_job_number} not found in system"
            }
    
    elif is_yes:
        # User confirmed - get suggested job from pending record
        suggested_job = pending_fields.get('JobNumber', '')
        
        if suggested_job:
            project = airtable.get_project(suggested_job)
            
            if project:
                airtable.log_traffic(
                    internet_message_id, conversation_id, 'update', 'processed',
                    suggested_job, suggested_job.split()[0], sender_email, subject
                )
                airtable.update_traffic_record(pending_clarify['id'], {'Status': 'resolved'})
                
                routing = {
                    'route': 'update',
                    'confidence': 'high',
                    'jobNumber': suggested_job,
                    'reason': 'User confirmed suggested job'
                }
                routing = enrich_with_project(routing, project)
                routing['clientCode'] = suggested_job.split()[0]
                
                payload = build_worker_payload(data, routing)
                
                # Call worker - worker handles file + update + comms
                worker_result = call_worker('update', payload)
                
                # If worker failed, send failure email
                if not worker_result.get('success'):
                    connect.send_failure(
                        to_email=sender_email,
                        route='update',
                        error_message=worker_result.get('error', 'Unknown error'),
                        sender_name=sender_name,
                        subject_line=subject,
                        job_number=suggested_job,
                        job_name=routing.get('jobName'),
                        client_name=routing.get('clientName'),
                        original_email=original_email
                    )
                
                return {
                    'route': 'update',
                    'confidence': 'high',
                    'reason': 'User confirmed suggested job',
                    'jobNumber': suggested_job,
                    'worker': worker_result
                }
    
    # Couldn't handle - return None to continue normal processing
    return None


# ===================
# WORKER PAYLOAD BUILDER
# ===================

def build_worker_payload(email_data, routing):
    """
    Build the payload that goes to workers.
    Includes everything the worker needs to do its job AND communicate results.
    """
    return {
        # Routing info
        'route': routing.get('route'),
        'type': routing.get('type', 'action'),
        
        # Job info
        'jobNumber': routing.get('jobNumber'),
        'jobName': routing.get('jobName'),
        'clientCode': routing.get('clientCode'),
        'clientName': routing.get('clientName'),
        
        # Project info (for Airtable updates)
        'projectRecordId': routing.get('projectRecordId'),
        'currentStage': routing.get('currentStage'),
        'currentStatus': routing.get('currentStatus'),
        'withClient': routing.get('withClient'),
        'filesUrl': routing.get('filesUrl'),
        
        # Teams info (for posting updates)
        'teamsChannelId': routing.get('teamsChannelId'),
        'teamId': routing.get('teamId'),
        
        # Email content (for worker to analyze)
        'emailContent': email_data.get('body') or email_data.get('content') or email_data.get('emailContent', ''),
        'subjectLine': email_data.get('subject') or email_data.get('subjectLine', ''),
        
        # Sender info (for confirmation emails)
        'senderName': email_data.get('senderName', ''),
        'senderEmail': email_data.get('from') or email_data.get('senderEmail', ''),
        
        # Original email (for email trail)
        'originalEmail': {
            'senderName': email_data.get('senderName', ''),
            'senderEmail': email_data.get('from') or email_data.get('senderEmail', ''),
            'subject': email_data.get('subject') or email_data.get('subjectLine', ''),
            'receivedDateTime': email_data.get('receivedDateTime', ''),
            'content': email_data.get('body') or email_data.get('content') or email_data.get('emailContent', '')
        },
        
        # Attachments (worker handles filing)
        'hasAttachments': email_data.get('hasAttachments', False),
        'attachmentNames': email_data.get('attachmentNames', []),
        'attachmentList': email_data.get('attachmentList', []),
        
        # Tracking
        'internetMessageId': email_data.get('internetMessageId', ''),
        'conversationId': email_data.get('conversationId', ''),
        'receivedDateTime': email_data.get('receivedDateTime', ''),
        'source': email_data.get('source', 'email'),
    }


# ===================
# RUN
# ===================

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
