"""
Dot Traffic 2.0
Intelligent routing layer for Hunch's agency workflow.
Receives emails from PA Listener or Hub, routes to workers.

FLOW:
1. Gates (ignore self, check sender domain, deduplication)
2. Check for pending clarify reply
3. Call Claude (Claude uses tools to fetch jobs, make decisions)
4. Log to Traffic table
5. Route response based on source (email → workers, hub → frontend)
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import airtable
import traffic
import connect

app = Flask(__name__)
CORS(app, resources={
    r"/*": {
        "origins": "*",
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type"]
    }
})


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
    Main routing endpoint. Receives requests from PA Listener (email) or Hub.
    Claude uses tools to identify client, fetch jobs, and decide.
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
        pending_clarify = None
        if conversation_id:
            pending_clarify = airtable.check_pending_clarify(conversation_id)
        
        if pending_clarify:
            result = handle_clarify_reply(data, pending_clarify)
            if result:
                return jsonify(result)
        
        # ===================
        # STEP 5: CALL CLAUDE
        # ===================
        # Claude identifies client, intent, and finds jobs using tools
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
        # STEP 6: LOG TO TRAFFIC TABLE
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
        # STEP 7: BUILD UNIVERSAL PAYLOAD
        # ===================
        payload = build_universal_payload(data, routing)
        
        # ===================
        # STEP 8: BUILD EMAIL (if clarify/confirm AND email source)
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
        # STEP 9: CALL DOWNSTREAM (source-aware)
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
        # STEP 10: SEND CONFIRMATION OR FAILURE EMAIL (email source only)
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
