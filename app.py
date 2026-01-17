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
import airtable
import traffic
import connect

app = Flask(__name__)


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
    5. If job-level intent with unclear job → add possibleJobs
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
        # Accept both our names and PA's names for flexibility
        content = data.get('body') or data.get('emailContent', '')
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
        # STEP 1: CHECK SENDER DOMAIN
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
        print(f"[app] Claude identified intent: {routing.get('route')}")
        
        if routing.get('route') == 'error':
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
                    # Valid job - enrich routing
                    routing = enrich_with_project(routing, project)
                    print(f"[app] Validated job: {job_number}")
                else:
                    # Job not found
                    routing['route'] = 'clarify'
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
                        routing['jobNumber'] = active_jobs[0]['jobNumber']
                        routing['confidence'] = 'high'
                        routing['reason'] = f"Only one active {client_code} job"
                        routing = enrich_with_project(routing, project)
                        print(f"[app] Single job match: {active_jobs[0]['jobNumber']}")
                else:
                    # Multiple jobs - need to confirm
                    routing['route'] = 'confirm'
                    routing['confidence'] = 'medium'
                    routing['clarifyType'] = 'confirm'
                    routing['possibleJobs'] = active_jobs[:5]  # Max 5
                    routing['originalIntent'] = routing.get('route', 'update')
                    print(f"[app] Multiple jobs - confirming with {len(active_jobs)} options")
            
            elif not client_code:
                # No client, no job - truly no idea
                routing['route'] = 'clarify'
                routing['confidence'] = 'low'
                routing['clarifyType'] = 'no_idea'
                print(f"[app] No client identified - clarifying")
        
        # ===================
        # STEP 7: VALIDATE CLIENT-LEVEL ROUTES
        # ===================
        client_level_intents = ['wip', 'tracker', 'incoming', 'triage']
        
        if routing.get('route') in client_level_intents:
            if not client_code:
                routing['route'] = 'clarify'
                routing['confidence'] = 'low'
                routing['clarifyType'] = 'no_idea'
                routing['reason'] = 'Could not identify client for this request'
                print(f"[app] Client-level intent but no client - clarifying")
            else:
                # Get client name for enrichment
                client_name = airtable.get_client_name(client_code)
                if client_name:
                    routing['clientName'] = client_name
        
        # ===================
        # STEP 8: LOG TO TRAFFIC TABLE
        # ===================
        route = routing.get('route', 'unknown')
        status = 'pending' if route in ['clarify', 'confirm'] else 'processed'
        
        airtable.log_traffic(
            internet_message_id,
            conversation_id,
            route,
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
        # STEP 10: BUILD EMAIL (if clarify/confirm)
        # ===================
        if route in ['clarify', 'confirm']:
            clarify_type = routing.get('clarifyType', 'no_idea')
            payload['emailHtml'] = connect.build_email(clarify_type, {
                **routing,
                'senderName': sender_name
            })
        
        # ===================
        # STEP 11: CALL DOWNSTREAM
        # ===================
        worker_result = connect.call_worker(route, payload)
        
        # ===================
        # RETURN RESPONSE
        # ===================
        return jsonify({
            'route': route,
            'confidence': routing.get('confidence', 'unknown'),
            'reason': routing.get('reason', ''),
            'jobNumber': routing.get('jobNumber'),
            'clientCode': routing.get('clientCode'),
            'clientName': routing.get('clientName'),
            'intent': routing.get('intent'),
            'worker': worker_result,
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
        'route': routing.get('route'),
        'confidence': routing.get('confidence'),
        'reasoning': routing.get('reason', ''),
        'intent': routing.get('intent'),
        
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
        
        # Clarify-specific
        'clarifyType': routing.get('clarifyType'),
        'possibleJobs': routing.get('possibleJobs'),
        'suggestedJob': routing.get('suggestedJob'),
        'originalIntent': routing.get('originalIntent'),
    }


# ===================
# RUN
# ===================

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
