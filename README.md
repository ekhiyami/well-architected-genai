
This solution takes a workload written in Infrastructure as Code (IaC), e.g., AWS CloudFormation, and calls **Amazon Bedrock** to analyze it, detect what AWS best practices are used (by comparing with best practices from the AWS Well-Architected Framework), and generate a list of the best practices used in this workload. It then interacts programmatically using the AWS Well-Architected Tool to create a **Well-Architected Review milestone**. This process takes **a minute or two** to complete **versus the manual process that traditionally takes several hours**.

Check a [walkthrough and a demo here](https://community.aws/content/2hYteYyGPff8nuzG3ye8HZQOtCf/how-i-cut-the-time-to-complete-a-well-architected-review-from-hours-to-minutes).

You can test this application in two ways:

1. Using the [deployed version here](https://wa-genai.streamlit.app/). You can provide your workload in CloudFormation, or you can use the sample one included in this repo.
2. By cloning this repo and updating its parameters for your local environment.

If you decide to go with [2], there are a few things you need to adjust in the `app.py` file:

0. Clone the repo to your machine.
1. Provide your credentials and the region in lines 17-19.
2. Create a workload in the AWS WA Tool. Retrieve its ID and provide it in line 22.
3. The code reads the latest version of the WA framework from a file stored in an S3 bucket. The best practices file is included in this repo. You need to create an S3 bucket and provide its name to the code in line 26.
5. Run the code using: `streamlit run app.py`
