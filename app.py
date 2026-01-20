"""
Dot Traffic 2.0
Intelligent routing layer for Hunch's agency workflow.
Receives emails from PA Listener, routes to workers.

REFACTORED: Claude-first approach
- Let Claude identify client and intent from raw email
- THEN fetch active jobs and enrich
- No more dumb regex extraction that confuses Claude
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import airtable
import traffic
import connect

app = Flask(__name__)
CORS(app)


# ===================
# HEALTH CHECK
# ===================

@app.route('/', methods=['GET'])
@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'Dot Traffic',
        'version': '2.1',
        'architecture': 'claude-first',
        'features': [
            'deduplication',
            'clarify-loop',
            'universal-payload',
            'route-registry',
            'smart-client-detection'
        ]
    })


# ===================
# MAIN TRAFFIC ENDPOINT
# ===================

@app.route('/traffic', methods=['POST'])
def handle_traffic():
    """
    Main routing endpoint. Receives email from PA Listener.
    
    CLAUDE-FIRST FLOW:
    1. Check for duplicate (already processed)
    2. Check for pending clarify reply
    3. Call Claude to identify client + intent (no pre-extraction)
    4. Fetch active jobs for Claude's identified client
    5. If job-level intent with unclear job ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ add possibleJobs
    6. Validate and enrich with project data
    7. Log to Traffic table
    8. Build universal payload
    9. Call downstream worker or PA Postman
    """
    try:
        data = request.get_json()
        
        # ===================
        # VALIDATE INPUT
        # ===================
        # Accept Hub's 'content', PA's 'body', or legacy 'emailContent'
        content = data.get('content') or data.get('body') or data.get('emailContent', '')
        if not content:
            return jsonify({'error': 'No email body provided'}), 400
        
        # Extract all email fields (accept PA names: body, subject, from, to, cc)
        subject = data.get('subject') or data.get('subjectLine', '')
        sender_email = data.get('from') or data.get('senderEmail', '')
        sender_name = data.get('senderName', '')
        all_recipients = data.get('to') or data.get('allRecipients', [])
        has_attachments = data.get('hasAttachments', False)
        attachment_names = data.get('attachmentNames', [])
        attachment_list = data.get('attachmentList', [])
        source = data.get('source', 'email')
        internet_message_id = data.get('internetMessageId', '')
        conversation_id = data.get('conversationId', '')
        received_datetime = data.get('receivedDateTime', '')
        
        # ===================
        # STEP 1: IGNORE DOT'S OWN EMAILS (prevent loops)
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
        # Only process emails from @hunch.co.nz
        if not sender_email.lower().endswith('@hunch.co.nz'):
            airtable.log_traffic(
                internet_message_id,
                conversation_id,
                'external',
                'ignored',
                None,
                None,
                sender_email,
                subject
            )
            return jsonify({
                'route': 'external',
                'status': 'ignored',
                'reason': 'External sender - only @hunch.co.nz emails are processed',
                'senderEmail': sender_email
            })
        
        # ===================
        # STEP 2: DEDUPLICATION
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
        # STEP 3: CHECK PENDING CLARIFY
        # ===================
        pending_clarify = None
        if conversation_id:
            pending_clarify = airtable.check_pending_clarify(conversation_id)
        
        if pending_clarify:
            result = handle_clarify_reply(data, pending_clarify)
            if result:
                return jsonify(result)
        
        # ===================
        # STEP 4: CLAUDE IDENTIFIES CLIENT + INTENT
        # ===================
        # No pre-extraction! Let Claude read the email naturally.
        # Only extract job numbers (structured data, regex is fine)
        job_number_hint = traffic.extract_job_number(subject)
        if not job_number_hint:
            job_number_hint = traffic.extract_job_number(content)
        if not job_number_hint and attachment_names:
            for filename in attachment_names:
                job_number_hint = traffic.extract_job_number(filename)
                if job_number_hint:
                    break
        
        print(f"[app] === CLAUDE-FIRST ROUTING ===")
        print(f"[app] Subject: {subject}")
        print(f"[app] Sender: {sender_email}")
        print(f"[app] Job number hint (regex): {job_number_hint}")
        
        # First Claude call - identify client and intent
        routing = traffic.route_email(data)
        
        print(f"[app] Claude identified client: {routing.get('clientCode')}")
        print(f"[app] Claude identified type: {routing.get('type')}")
        print(f"[app] Claude identified route: {routing.get('route')}")
        
        if routing.get('type') == 'error' or routing.get('route') == 'error':
            return jsonify(routing), 500
        
        # ===================
        # STEP 5: FETCH ACTIVE JOBS FOR CLAUDE'S CLIENT
        # ===================
        client_code = routing.get('clientCode')
        active_jobs = []
        
        if client_code:
            active_jobs = airtable.get_active_jobs(client_code)
            print(f"[app] Fetched {len(active_jobs)} active jobs for {client_code}")
        
        # ===================
        # STEP 6: HANDLE JOB-LEVEL INTENTS
        # ===================
        # If Claude identified a job-level intent but no specific job,
        # we need to show options (confirm) or validate the job
        
        job_level_intents = ['update', 'file', 'feedback', 'work-to-client']
        
        if routing.get('route') in job_level_intents:
            job_number = routing.get('jobNumber')
            
            if job_number:
                # Claude identified a specific job - validate it
                project = airtable.get_project(job_number)
                if project:
                    # Valid job - enrich routing and ensure type is action
                    routing['type'] = 'action'
                    routing = enrich_with_project(routing, project)
                    print(f"[app] Validated job: {job_number}")
                else:
                    # Job not found
                    routing['type'] = 'clarify'
                    routing['confidence'] = 'low'
                    routing['clarifyType'] = 'job_not_found'
                    routing['reason'] = f"Job {job_number} not found in system"
                    print(f"[app] Job not found: {job_number}")
            
            elif active_jobs:
                # No specific job but we have options - confirm with list
                if len(active_jobs) == 1:
                    # Only one job - assume it's the one
                    project = airtable.get_project(active_jobs[0]['jobNumber'])
                    if project:
                        routing['type'] = 'action'
                        routing['jobNumber'] = active_jobs[0]['jobNumber']
                        routing['confidence'] = 'high'
                        routing['reason'] = f"Only one active {client_code} job"
                        routing = enrich_with_project(routing, project)
                        print(f"[app] Single job match: {active_jobs[0]['jobNumber']}")
                else:
                    # Multiple jobs - need to confirm
                    routing['type'] = 'confirm'
                    routing['confidence'] = 'medium'
                    routing['possibleJobs'] = active_jobs[:5]  # Max 5
                    routing['originalIntent'] = routing.get('route', 'update')
                    print(f"[app] Multiple jobs - confirming with {len(active_jobs)} options")
            
            elif not client_code:
                # No client, no job - truly no idea
                routing['type'] = 'clarify'
                routing['confidence'] = 'low'
                routing['clarifyType'] = 'no_idea'
                print(f"[app] No client identified - clarifying")
        
        # ===================
        # STEP 7: VALIDATE CLIENT-LEVEL ROUTES
        # ===================
        client_level_intents = ['wip', 'tracker', 'incoming', 'triage']
        
        if routing.get('route') in client_level_intents:
            if not client_code:
                routing['type'] = 'clarify'
                routing['confidence'] = 'low'
                routing['clarifyType'] = 'no_idea'
                routing['reason'] = 'Could not identify client for this request'
                print(f"[app] Client-level intent but no client - clarifying")
            else:
                # Valid client-level action
                routing['type'] = 'action'
                # Get client name for enrichment
                client_name = airtable.get_client_name(client_code)
                if client_name:
                    routing['clientName'] = client_name
        
        # ===================
        # STEP 8: LOG TO TRAFFIC TABLE
        # ===================
        # Determine response type - with backwards compatibility
        response_type = routing.get('type')
        route = routing.get('route', 'unknown')
        
        if not response_type:
            # Backwards compat: infer type from route
            if route in ['clarify', 'confirm']:
                response_type = route
            else:
                response_type = 'action'
        
        # For logging, use type if it's clarify/confirm, otherwise use route
        log_route = response_type if response_type in ['clarify', 'confirm', 'answer', 'redirect'] else route
        status = 'pending' if response_type in ['clarify', 'confirm'] else 'processed'
        
        airtable.log_traffic(
            internet_message_id,
            conversation_id,
            log_route,
            status,
            routing.get('jobNumber'),
            routing.get('clientCode'),
            sender_email,
            subject
        )
        
        # ===================
        # STEP 9: BUILD UNIVERSAL PAYLOAD
        # ===================
        payload = build_universal_payload(data, routing)
        
        # ===================
        # STEP 10: BUILD EMAIL (if clarify/confirm AND email source)
        # ===================
        if response_type in ['clarify', 'confirm'] and source == 'email':
            clarify_type = routing.get('clarifyType', 'no_idea')
            # For confirm, use 'confirm' as the template type
            if response_type == 'confirm':
                clarify_type = 'confirm'
            payload['emailHtml'] = connect.build_email(clarify_type, {
                **routing,
                'senderName': sender_name,
                'senderEmail': sender_email,
                'subjectLine': subject,
                'receivedDateTime': received_datetime,
                'emailContent': content
            })
        
        # ===================
        # STEP 11: CALL DOWNSTREAM (source-aware)
        # ===================
        worker_result = None
        
        if response_type == 'action':
            if source == 'email':
                # Route to worker
                worker_result = connect.call_worker(route, payload)
            else:
                # Hub - don't call workers, return for user to act on
                # (e.g., show job card for them to update themselves)
                worker_result = {'success': True, 'status': 'user_action_required'}
        elif response_type in ['clarify', 'confirm']:
            if source == 'email':
                # Send clarification email via PA Postman
                worker_result = connect.call_worker(response_type, payload)
            else:
                # Hub - just return, frontend will render the message/cards
                worker_result = {'success': True, 'status': 'pending_user_input'}
        elif response_type == 'answer':
            if source == 'email':
                # Build original email data for trail
                original_email_data = {
                    'senderName': sender_name,
                    'senderEmail': sender_email,
                    'subject': subject,
                    'receivedDateTime': received_datetime,
                    'content': content
                }
                
                # Send answer email via PA Postman
                worker_result = connect.send_answer(
                    to_email=sender_email,
                    message=routing.get('message', ''),
                    sender_name=sender_name,
                    subject_line=subject,
                    client_code=routing.get('clientCode'),
                    client_name=routing.get('clientName'),
                    original_email=original_email_data
                )
            else:
                # Hub - just return, frontend will render the message
                worker_result = {'success': True, 'status': 'answered'}
        elif response_type == 'redirect':
            if source == 'email':
                # Build original email data for trail
                original_email_data = {
                    'senderName': sender_name,
                    'senderEmail': sender_email,
                    'subject': subject,
                    'receivedDateTime': received_datetime,
                    'content': content
                }
                
                # Send redirect email via PA Postman
                worker_result = connect.send_redirect(
                    to_email=sender_email,
                    message=routing.get('message', ''),
                    sender_name=sender_name,
                    subject_line=subject,
                    client_code=routing.get('clientCode'),
                    client_name=routing.get('clientName'),
                    redirect_to=routing.get('redirectTo', 'WIP'),
                    original_email=original_email_data
                )
            else:
                # Hub - just return, frontend will render the redirect
                worker_result = {'success': True, 'status': 'redirected'}
        else:
            # Unknown type - try calling as route for backwards compat (email only)
            if source == 'email':
                worker_result = connect.call_worker(route, payload)
            else:
                worker_result = {'success': False, 'status': 'unknown_type', 'error': f'Unknown type: {response_type}'}
        
        # ===================
        # STEP 12: SEND CONFIRMATION OR FAILURE EMAIL (email source only)
        # ===================
        confirmation_result = None
        
        # Only send confirmation emails for email source actions
        if source == 'email' and response_type == 'action':
            if worker_result and worker_result.get('success'):
                # Get files URL from worker response if available
                files_url = worker_result.get('response', {}).get('folderUrl') if isinstance(worker_result.get('response'), dict) else None
                
                # Build original email data for trail
                original_email_data = {
                    'senderName': sender_name,
                    'senderEmail': sender_email,
                    'subject': subject,
                    'receivedDateTime': received_datetime,
                    'content': content
                }
                
                confirmation_result = connect.send_confirmation(
                    to_email=sender_email,
                    route=route,
                    sender_name=sender_name,
                    client_name=routing.get('clientName'),
                    job_number=routing.get('jobNumber'),
                    job_name=routing.get('jobName'),
                    subject_line=subject,
                    files_url=files_url,
                    original_email=original_email_data
                )
            elif worker_result:
                # Worker failed - send failure notification
                error_message = worker_result.get('error') or worker_result.get('response') or 'Unknown error'
                # Build original email data for trail
                original_email_data = {
                    'senderName': sender_name,
                    'senderEmail': sender_email,
                    'subject': subject,
                    'receivedDateTime': received_datetime,
                    'content': content
                }
                
                confirmation_result = connect.send_failure(
                    to_email=sender_email,
                    route=route,
                    error_message=str(error_message),
                    sender_name=sender_name,
                    subject_line=subject,
                    job_number=routing.get('jobNumber'),
                    job_name=routing.get('jobName'),
                    client_name=routing.get('clientName'),
                    original_email=original_email_data
                )
        
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
            'originalIntent': routing.get('originalIntent'),
            'clarifyType': routing.get('clarifyType'),
            'redirectTo': routing.get('redirectTo'),
            'redirectParams': routing.get('redirectParams'),
            'nextPrompt': routing.get('nextPrompt'),
            'worker': worker_result,
            'confirmation': confirmation_result,
            'payload': payload
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
    
    pending_fields = pending_clarify['fields']
    
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
        
        payload = build_universal_payload(data, {
            'route': 'triage',
            'confidence': 'high',
            'reason': 'User requested triage in clarify reply'
        })
        
        worker_result = connect.call_worker('triage', payload)
        
        return {
            'route': 'triage',
            'confidence': 'high',
            'reason': 'User requested triage in clarify reply',
            'worker': worker_result,
            'payload': payload
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
            
            payload = build_universal_payload(data, routing)
            worker_result = connect.call_worker('update', payload)
            
            return {
                'route': 'update',
                'confidence': 'high',
                'reason': 'Job number provided in clarify reply',
                'jobNumber': reply_job_number,
                'worker': worker_result,
                'payload': payload
            }
        else:
            # Invalid job number - clarify again
            routing = {
                'route': 'clarify',
                'confidence': 'low',
                'clarifyType': 'job_not_found',
                'jobNumber': reply_job_number,
                'reason': f"Job {reply_job_number} not found in system"
            }
            
            payload = build_universal_payload(data, routing)
            payload['emailHtml'] = connect.build_email('job_not_found', {
                **routing,
                'senderName': sender_name,
                'jobNumber': reply_job_number
            })
            
            worker_result = connect.call_worker('clarify', payload)
            
            return {
                'route': 'clarify',
                'confidence': 'low',
                'reason': f"Job {reply_job_number} not found in system",
                'worker': worker_result,
                'payload': payload
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
                
                payload = build_universal_payload(data, routing)
                worker_result = connect.call_worker('update', payload)
                
                return {
                    'route': 'update',
                    'confidence': 'high',
                    'reason': 'User confirmed suggested job',
                    'jobNumber': suggested_job,
                    'worker': worker_result,
                    'payload': payload
                }
    
    # Couldn't handle - return None to continue normal processing
    return None


# ===================
# UNIVERSAL PAYLOAD BUILDER
# ===================

def build_universal_payload(email_data, routing):
    """
    Build the universal payload that goes to all workers.
    
    Args:
        email_data: Original email data from PA Listener
        routing: Routing decision from Claude + enrichment
    
    Returns:
        dict with all fields any worker might need
    """
    return {
        # Routing
        'type': routing.get('type', 'action'),
        'route': routing.get('route'),
        'confidence': routing.get('confidence'),
        'reasoning': routing.get('reason', ''),
        'intent': routing.get('intent'),
        'message': routing.get('message', ''),
        
        # Job
        'jobNumber': routing.get('jobNumber'),
        'clientCode': routing.get('clientCode'),
        'clientName': routing.get('clientName'),
        
        # Project (from Airtable)
        'projectRecordId': routing.get('projectRecordId'),
        'teamsChannelId': routing.get('teamsChannelId'),
        'teamId': routing.get('teamId'),
        'currentStage': routing.get('currentStage'),
        'currentStatus': routing.get('currentStatus'),
        'withClient': routing.get('withClient'),
        
        # Sender (accept both PA names and our names)
        'senderName': email_data.get('senderName', ''),
        'senderEmail': email_data.get('from') or email_data.get('senderEmail', ''),
        'allRecipients': email_data.get('to') or email_data.get('allRecipients', []),
        
        # Content (accept both PA names and our names)
        'subjectLine': email_data.get('subject') or email_data.get('subjectLine', ''),
        'emailContent': email_data.get('body') or email_data.get('emailContent', ''),
        
        # Attachments
        'hasAttachments': email_data.get('hasAttachments', False),
        'attachmentNames': email_data.get('attachmentNames', []),
        'attachmentList': email_data.get('attachmentList', []),
        
        # Tracking
        'internetMessageId': email_data.get('internetMessageId', ''),
        'conversationId': email_data.get('conversationId', ''),
        'receivedDateTime': email_data.get('receivedDateTime', ''),
        'source': email_data.get('source', 'email'),
        
        # Clarify/Confirm-specific
        'clarifyType': routing.get('clarifyType'),
        'possibleJobs': routing.get('possibleJobs'),
        'jobs': routing.get('jobs'),
        'suggestedJob': routing.get('suggestedJob'),
        'originalIntent': routing.get('originalIntent'),
        
        # Redirect-specific
        'redirectTo': routing.get('redirectTo'),
        'redirectParams': routing.get('redirectParams'),
        
        # Hub-specific
        'nextPrompt': routing.get('nextPrompt'),
    }


# ===================
# RUN
# ===================

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
