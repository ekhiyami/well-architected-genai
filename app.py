import streamlit as st
import boto3
import json
from botocore.exceptions import ClientError
from botocore.credentials import Credentials
import pandas as pd
import csv
import re
import os
import uuid
import base64
import tempfile
from datetime import datetime
from io import StringIO

# Access secrets using st.secrets
aws_access_key_id = st.secrets["aws_access_key_id"]
aws_secret_access_key = st.secrets["aws_secret_access_key"]
region_name = st.secrets["region"]

# Specify the workload parameters
workload_id = st.secrets["workload_id"]
lens_alias = 'wellarchitected'

# AWS S3 Configuration
s3_bucket = st.secrets["s3_bucket"] 

# Initialize AWS clients
s3_client = boto3.client(
    's3',
    aws_access_key_id=aws_access_key_id,
    aws_secret_access_key=aws_secret_access_key,
    region_name=region_name
)

bedrock_client = boto3.client(
    'bedrock-runtime',
    region_name=region_name,
    aws_access_key_id=aws_access_key_id,
    aws_secret_access_key=aws_secret_access_key
)

wa_client = boto3.client(
    'wellarchitected',
    region_name=region_name,
    aws_access_key_id=aws_access_key_id,
    aws_secret_access_key=aws_secret_access_key
) 
# Inject custom CSS for the expander
st.markdown(
    """
    <style>
    span[class="st-emotion-cache-1dtefog eqpbllx2"] p {
        font-size: 20px !important; /* change 20px to increase or decrease the size */
    }
    </style>
    """,
    unsafe_allow_html=True
)

##Functions related to Analyze button
def upload_file_to_s3(uploaded_file, s3_bucket):
    try:
        s3_client.upload_fileobj(uploaded_file, s3_bucket, uploaded_file.name)
        file_url = f"https://{s3_bucket}.s3.{s3_client.meta.region_name}.amazonaws.com/{uploaded_file.name}"
        #st.success(f"File uploaded successfully! URL: {file_url}")
        st.success(f"Your workloads received successfully!")
        return file_url
    except ClientError as e:
        st.write(f"Access Key ID: {aws_access_key_id[:5]}...")  # Print only first 5 characters for security
        st.write(f"Secret Access Key: {aws_secret_access_key[:5]}...")
        st.write(f"Region: {region_name}")
        st.write(f"S3 Bucket: {s3_bucket}")
        st.error(f"Error uploading file to S3: {e}")
        return None

def analyze_template_with_bedrock(s3_url, best_practices_json_path):
    model_id = "anthropic.claude-3-sonnet-20240229-v1:0"
    # Load the best practices JSON from the file
    try:
        # Get the object from S3
        response = s3_client.get_object(Bucket=s3_bucket, Key=best_practices_json_path)

        # Read the content of the file
        content = response['Body'].read().decode('utf-8')

        # Parse the JSON content
        best_practices = json.loads(content)

    except ClientError as e:
        print(f"Error reading file from S3: {e}")
        return None

    # Convert the best practices to a formatted JSON string
    best_practices_json = json.dumps(best_practices, indent=2)

    user_message = f"""
    Analyze the following CloudFormation template from s3 URL:
    {s3_url}
    
    For each of the following best practices from the AWS Well-Architected Framework, determine if it is applied in the given CloudFormation template s3 URL. 

    Best Practices:
    {best_practices_json}
    
    For each best practice, respond in the following EXACT format only: 
    [Exact Best Practice Name as given in Best Practices]: [Why do you consider this best practice applicable?]

    IMPORTANT: Use the EXACT best practice name as given in the Best Practices. 

    Do not rephrase or summarize the practice name. List only the practices which are Applied
    """
    #for debugging
    #print(user_message)

    request_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4096,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": user_message
                    }
                ]
            }
        ]
    }

    try:
        response = bedrock_client.invoke_model(
            modelId=model_id,
            contentType='application/json',
            accept='application/json',
            body=json.dumps(request_body)
        )
        
        response_body = json.loads(response['body'].read())
        analysis_content = response_body.get('content', [])
        
        analysis_result = "\n".join(
            item['text'] for item in analysis_content if item['type'] == 'text'
        )
        #for debugging
        #print(analysis_result)
        return analysis_result

    except ClientError as e:
        error_code = e.response['Error']['Code']
        error_message = e.response['Error']['Message']
        st.error(f"AWS Error: {error_code} - {error_message}")
        st.error("Please check your AWS credentials and permissions.")
        return None

def display_result(analysis_results, file_path):
    pattern = re.compile(r'\[(.*?)\]:\s*(.*)')
    matches = pattern.findall(analysis_results)
    
    response = s3_client.get_object(Bucket=s3_bucket, Key=file_path)
    content = response['Body'].read().decode('utf-8')
    best_practices = pd.read_csv(StringIO(content))
    
    if best_practices.empty:
        st.error("No best practices could be loaded. Please check the file and try again.")
        return
    
    pillars = {}
    for index, row in best_practices.iterrows():
        pillar = row.get('Pillar', 'Unknown')
        question = row.get('Question', 'Unknown')
        practice = row.get('Best Practice', '')
        
        if pillar not in pillars:
            pillars[pillar] = {}
        if question not in pillars[pillar]:
            pillars[pillar][question] = []
        pillars[pillar][question].append(practice)
    
    st.title("BPs found in your architecture")
    for pillar, questions in pillars.items():
        with st.expander(f"**{pillar}**", expanded=False):

        # Get the pillar ID
            pillar_id = None
            lens_review_response = wa_client.get_lens_review(
                WorkloadId=workload_id,
                LensAlias=lens_alias
            )
            for pillar_summary in lens_review_response.get('LensReview', {}).get('PillarReviewSummaries', []):
                if pillar_summary.get('PillarName') == pillar:
                    pillar_id = pillar_summary.get('PillarId')
                    break
        
            if not pillar_id:
                print(f"Couldn't find PillarId for {pillar}. Skipping...")
                continue
            
            # Initialize pagination variables
            next_token = None
            
            while True:
                # Build the API request parameters
                params = {
                    'WorkloadId': workload_id,
                    'LensAlias': lens_alias,
                    'PillarId': pillar_id
                }
                if next_token:
                    params['NextToken'] = next_token
                
                # Get answers for each question under the current pillar
                answers_response = wa_client.list_answers(**params)
                
                for answer in answers_response['AnswerSummaries']:
                    question_title = answer['QuestionTitle']
                    selected_choices = answer['SelectedChoices']
                    
                    for question, practices in questions.items():
                        before_dash, separator, after_dash = question.partition(' - ')
                        if after_dash == question_title:
                            st.session_state.update_button_enabled = True
                            applied_practices = []
                            
                            choice_title_to_id = {choice['Title']: choice['ChoiceId'] for choice in answer.get('Choices', [])}
                        
                            for practice in practices:
                                practice_text = ' '.join(practice.split(' ')[1:]).strip()
                                if any(practice_text in choice['Title'] for choice in answer.get('Choices', []) if choice['ChoiceId'] in selected_choices):
                                    applied_practices.append((practice, "Previously Applied"))
                            
                                for key, reason in matches:
                                    if key.strip() == practice.strip():
                                        if not any(practice == item[0] for item in applied_practices):
                                            applied_practices.append((practice, reason))
                            # Display the question and its applied practices if any are applied
                            if applied_practices:
                                st.markdown(f"**{question}**")
                                st.session_state.update_button_enabled = True
                                for practice, reason in applied_practices:
                                    if reason == "Previously Applied":
                                        st.markdown(f"✔️ {practice}")
                                        #st.markdown(f"   Reason: {reason}")
                                    else:
                                        st.markdown(f"✔️ {practice}")
                                        #st.markdown(f"   Reason: {reason}")
            
                # Check if there are more results
                next_token = answers_response.get('NextToken')
                if not next_token:
                    break

    # Enable the update button at the end of the function
    st.session_state.update_button_enabled = True

##Functions related to Update Button
def update_workload(analysis_results, file_path):
    # Fetch workload and lens review details
    workload_response = wa_client.get_workload(WorkloadId=workload_id)
    lens_review_response = wa_client.get_lens_review(WorkloadId=workload_id, LensAlias=lens_alias)

    # Read best practices from S3
    response = s3_client.get_object(Bucket=s3_bucket, Key=file_path)
    content = response['Body'].read().decode('utf-8')
    best_practices = pd.read_csv(StringIO(content))

    # Parse analysis results
    analysis_bp_list = [key for key, value in re.findall(r'\[(.*?)\]:\s*(.*?)', analysis_results)]

    # Create mappings from Best Practice to Pillar and Question
    practice_to_pillar_question = {}
    for index, row in best_practices.iterrows():
        pillar = row.get('Pillar', '').strip().lower()
        question = row.get('Question', '').strip().lower()
        practice = row.get('Best Practice', '').strip()
        
        # Remove all spaces from the pillar
        pillar_no_spaces = pillar.replace(' ', '')
        # Initialize the dictionary entry if it does not exist
        if pillar_no_spaces not in practice_to_pillar_question:
            practice_to_pillar_question[pillar_no_spaces] = []

        for bp in analysis_bp_list:
            if bp == practice:
                practice_text = ' '.join(practice.split(' ')[1:]).strip().lower()
                before_dash, separator, after_dash = question.partition(' - ')
                practice_to_pillar_question[pillar_no_spaces].append({
                        'Question': after_dash,
                        'Practice': practice_text
                })

    # Iterate over Pillar IDs from the Lens Review response
    for pillar_summary in lens_review_response.get('LensReview', {}).get('PillarReviewSummaries', []):
        pillar_id = pillar_summary.get('PillarId', 'No PillarId')
        print(f"Processing Pillar ID: {pillar_id}")

        # Initialize pagination variables
        next_token = None

        while True:
            try:
                # Build the API request parameters
                params = {
                    'WorkloadId': workload_id,
                    'LensAlias': lens_alias,
                    'PillarId': pillar_id
                }
                if next_token:
                    params['NextToken'] = next_token
                
                # Get questions for this pillar
                questions_response = wa_client.list_answers(**params)
                
                # Print the response for debugging
                #print(f"Questions response: {json.dumps(questions_response, indent=4)}")

                # Process questions
                for question in questions_response.get('AnswerSummaries', []):
                    question_id = question.get('QuestionId', 'No QuestionId')
                    question_title = question.get('QuestionTitle', 'No QuestionTitle')
                    current_choices = question.get('SelectedChoices', [])
                    updated_choices = current_choices
                    print(f"Processing Question: {question_title}")

                    # Iterate over the details list for the current pillar
                    for key in practice_to_pillar_question.keys():
                        if key.startswith(pillar_id.lower()):
                            print(f"Key matched: {key}")
                            for entry in practice_to_pillar_question[key]:
                                practice1 = entry.get('Practice', 'No Practice')
                                question1 = entry.get('Question', 'No Question')
                                new_choice_ids = []
                                if question1 == question_title.lower():
                                    #print(f"Question matched: {question1}")
                                    choice_title_to_id = {choice['Title']: choice['ChoiceId'] for choice in question.get('Choices', [])}
                                    for new_choice_title, choice_id in choice_title_to_id.items():
                                        if new_choice_title.lower() == practice1:
                                            print(f"Practice matched: {practice1}")
                                            new_choice_ids.append(choice_id)
                                    #print(f"new_choice_ids = {new_choice_ids}")

                                    updated_choices = list(set(updated_choices + new_choice_ids))  # Remove duplicates
                                    # Update the answer with the merged choices
                                    wa_client.update_answer(
                                       WorkloadId=workload_id,
                                       LensAlias=lens_alias,
                                       QuestionId=question_id,
                                       SelectedChoices=updated_choices,
                                       Notes='Updated during review process'
                                    )
                                    print(f"Updated Question Title: {question_title} with Choices: {updated_choices}")

                # Check if there is a next token
                next_token = questions_response.get('NextToken')
                if not next_token:
                    break  # Exit the loop if no more pages are available

            except ClientError as e:
                print(f"Error retrieving or updating answers for Pillar ID {pillar_id}: {e}")
                return e

    create_milestone()
    st.session_state.report_button_enabled = True
    return "Success"

def create_milestone():
    # Define a milestone name with current date and time
    current_datetime = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    milestone_name = f'Review completed on {current_datetime}'
    client_request_token = str(uuid.uuid4())  # Generate a unique client request token
  
    try:
        milestone_response = wa_client.create_milestone(
            WorkloadId=workload_id,
            MilestoneName=milestone_name,
            ClientRequestToken=client_request_token
        )
        print("Milestone created")

    except Exception as e:
        print(f"Error creating milestone: {e}")

def summarize_risks(workload_id, lens_alias):
    # Initialize counters for different risk levels
    pillar_summaries = {}
    total_questions = 0
    answered_questions = 0

    # Retrieve all pillars for the lens review
    lens_review_response = wa_client.get_lens_review(
        WorkloadId=workload_id,
        LensAlias=lens_alias
    )

    # Loop through each pillar and list answers for each pillar
    for pillar_summary in lens_review_response.get('LensReview', {}).get('PillarReviewSummaries', []):
        pillar_id = pillar_summary.get('PillarId', 'No PillarId')
        pillar_name = pillar_summary.get('PillarName', 'Unknown Pillar')

        pillar_summaries[pillar_id] = {
            'name': pillar_name,
            'total': 0,
            'answered': 0,
            'high': 0,
            'medium': 0,
        }

        # Initialize pagination variables
        next_token = None

        while True:
            try:
                # Build the API request parameters
                params = {
                    'WorkloadId': workload_id,
                    'LensAlias': lens_alias,
                    'PillarId': pillar_id
                }
                if next_token:
                    params['NextToken'] = next_token
                
                # Get answers for each question under the current pillar
                answers_response = wa_client.list_answers(**params)

                for answer_summary in answers_response.get('AnswerSummaries', []):
                    pillar_summaries[pillar_id]['total'] += 1
                    total_questions += 1
                    risk = answer_summary.get('Risk', 'UNANSWERED')
                    if risk != 'UNANSWERED':
                        pillar_summaries[pillar_id]['answered'] += 1
                        answered_questions += 1
                    if risk == 'HIGH':
                        pillar_summaries[pillar_id]['high'] += 1
                    elif risk == 'MEDIUM':
                        pillar_summaries[pillar_id]['medium'] += 1

                # Check if there is a next token
                next_token = answers_response.get('NextToken')
                if not next_token:
                    break  # Exit the loop if no more pages are available

            except ClientError as e:
                print(f"Error retrieving answers for Pillar ID {pillar_id}: {e}")
                break  # Exit the loop on error to prevent infinite retries

    return pillar_summaries, total_questions, answered_questions


def display_risk_summary(pillar_summaries, total_questions, answered_questions):
    # Display the summary of risks on the Streamlit interface
    st.subheader("Risk Summary")
    st.markdown(f"Questions Answered: {answered_questions}/{total_questions}")
    
    # Initialize counters for overall risk levels
    total_high = 0
    total_medium = 0
    
    # Sum up the risks across all pillars
    for pillar_data in pillar_summaries.values():
        total_high += pillar_data['high']
        total_medium += pillar_data['medium']
    
    # Display overall risk metrics
    col1, col2 = st.columns(2)
    col1.markdown(f"<h3 style='color: red;'>High Risks: {total_high}</h3>", unsafe_allow_html=True)
    col2.markdown(f"<h3 style='color: orange;'>Medium Risks: {total_medium}</h3>", unsafe_allow_html=True)
    
    # Display risk breakdown by pillar in a table
    st.subheader("Risk Breakdown by Pillar")
    
    # Prepare data for the table
    table_data = []
    for pillar_id, pillar_data in pillar_summaries.items():
        table_data.append({
            "Pillar": pillar_data['name'],
            "Questions Answered": f"{pillar_data['answered']}/{pillar_data['total']}",
            "High Risks": pillar_data['high'],
            "Medium Risks": pillar_data['medium'],
        })
    
    # Create a DataFrame and display it as a table
    df = pd.DataFrame(table_data)
    df = df.reset_index(drop=True)
    
    html = df.to_html(index=False)

    st.markdown(html, unsafe_allow_html=True)

#Functions related to Generate Button
def generate_and_download_report(workload_id, lens_alias):
    try:
        # Generate the report using GetLensReviewReport API
        response = wa_client.get_lens_review_report(
            WorkloadId=workload_id,
            LensAlias=lens_alias
        )
        
        # Extract the Base64 encoded report data
        base64_string = response.get('LensReviewReport', {}).get('Base64String')
        
        if not base64_string:
            st.error("Failed to retrieve the report data.")
            return None
        
        # Decode the Base64 string
        report_data = base64.b64decode(base64_string)
        
        # Create a download link
        b64 = base64.b64encode(report_data).decode()
        href = f'<a href="data:application/pdf;base64,{b64}" download="WA_Review_Report_{workload_id}.pdf">Click here to download the report</a>'
        st.markdown(href, unsafe_allow_html=True)
        
        return "Report generated successfully"
    
    except Exception as e:
        st.error(f"Error generating report: {str(e)}")
        return None
    
    except ClientError as e:
        error_code = e.response['Error']['Code']
        error_message = e.response['Error']['Message']
        st.error(f"AWS Error: {error_code} - {error_message}")
        if error_code == "ValidationException":
            st.error("Please check if the WorkloadId and LensAlias are correct.")
        elif error_code == "ResourceNotFoundException":
            st.error("The specified workload or lens was not found.")
        elif error_code == "AccessDeniedException":
            st.error("You don't have permission to perform this operation. Check your IAM policies.")
        else:
            st.error("Please check your AWS credentials and permissions.")
        return None
    except Exception as e:
        st.error(f"Unexpected error: {str(e)}")
        return None

#Functions related to display
def analyze_callback():
    st.session_state.update_disabled = False
    st.session_state.report_disabled = True
    
def update_callback():
    st.session_state.report_disabled = False

# Main App
def main():
    st.title("Are you Well-Architected? ✅")

    
    best_practices_file_path = 'well_architected_best_practices.json'
    best_practices_csv_path = 'well_architected_best_practices.csv'

    # Initialize session state variables
    if 'analysis_result' not in st.session_state:
        st.session_state.analysis_result = None
    if 'analyze_disabled' not in st.session_state:
        st.session_state.analyze_disabled = False
    if 'analyze_click' not in st.session_state:
        st.session_state.analyze_click = 1
    if 'update_click' not in st.session_state:
        st.session_state.update_click = 1
    if 'report_click' not in st.session_state:
        st.session_state.report_click = 1
    if 'report_link' not in st.session_state:
        st.session_state.report_link = None
    if 'update_disabled' not in st.session_state:
        st.session_state.update_disabled = True
    if 'report_disabled' not in st.session_state:
        st.session_state.report_disabled = True

  

    uploaded_file = st.file_uploader("I will analyze your workload for AWS best practices and complete a Well-Architected Review in seconds", type=["yaml", "json", "yml"])
    
    if uploaded_file is not None:
        s3_url = upload_file_to_s3(uploaded_file, s3_bucket)

        col1, col2, col3 = st.columns(3)

        with col1:
            analyze_button = st.button("AWS best practices I'm Using!", key='analyze_button', 
                                       on_click=analyze_callback, 
                                       disabled=st.session_state.analyze_disabled)
        with col2:
            update_button = st.button("Complete a WA Review", key='update_button', 
                                      on_click=update_callback,
                                      disabled=st.session_state.update_disabled)
        with col3:
            report_button = st.button("Show me Detailed Report", key='report_button',
                                      disabled=st.session_state.report_disabled)
        
        if s3_url and analyze_button:
            if st.session_state.analyze_click == 1:
                with st.spinner('Checking your workloads for AWS best practices...'):
                    analysis_results = analyze_template_with_bedrock(s3_url, best_practices_file_path)
                    st.session_state.analyze_click += 1
                    st.session_state.analysis_result = analysis_results
                    if st.session_state.analysis_result:
                        display_result(st.session_state.analysis_result, best_practices_csv_path)
                    else:
                        st.error("Failed to analyze the template. Please try again.")
                        st.session_state.update_disabled = True
                        st.session_state.report_disabled = True
            else:
                display_result(st.session_state.analysis_result, best_practices_csv_path)
 
        if update_button and st.session_state.analysis_result:
            if st.session_state.update_click == 1:
                with st.spinner('Updating Well-Architeced Review...'):
                    status = update_workload(st.session_state.analysis_result, best_practices_csv_path)
                    if status == "Success":
                        st.markdown("Well-Architected Review updated and a Milestone created")
                        st.session_state.update_click += 1
        
                        pillar_summaries, total_questions, answered_questions = summarize_risks(workload_id, lens_alias)
                        display_risk_summary(pillar_summaries, total_questions, answered_questions)
                    else:
                        st.write(f"Error in updating workload: {status}")
                        st.session_state.update_disabled = False
                        st.session_state.report_disabled = True
            else:
                pillar_summaries, total_questions, answered_questions = summarize_risks(workload_id, lens_alias)
                display_risk_summary(pillar_summaries, total_questions, answered_questions)
        
        # Display report download link
        if report_button and st.session_state.analysis_result:
            if st.session_state.report_click == 1:
                with st.spinner("Generating Well-Architected Report..."):
                    try:
                        response = wa_client.get_lens_review_report(
                            WorkloadId=workload_id,
                            LensAlias=lens_alias
                        )
                        base64_string = response.get('LensReviewReport', {}).get('Base64String')
                        if base64_string:
                            b64 = base64.b64encode(base64.b64decode(base64_string)).decode()
                            href = f'<a href="data:application/pdf;base64,{b64}" download="WA_Review_Report_{workload_id}.pdf">Click here to download the report</a>'
                            st.session_state.report_link = href
                            st.markdown(href, unsafe_allow_html=True)
                            st.session_state.report_click += 1
                            st.success("Report generated successfully. Click the download button above to save the PDF.")
                        else:
                            st.error("Failed to retrieve the report data.")
                    except Exception as e:
                        st.error(f"Error generating report: {str(e)}")
            else:
                st.markdown(st.session_state.report_link, unsafe_allow_html=True)
                st.success("Report generated successfully. Click the download button above to save the PDF.")

    
# Run the app
if __name__ == "__main__":
    main()
