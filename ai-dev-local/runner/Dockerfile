FROM node:20-bullseye

RUN apt-get update && apt-get install -y git curl bash ca-certificates python3 python3-pip && rm -rf /var/lib/apt/lists/*

# Install aider (Python-based CLI code assistant)
RUN pip3 install --no-cache-dir aider-chat

WORKDIR /workspace
COPY .cursor /workspace/.cursor
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENV GH_TOKEN=""
ENV REPO_URL=""
ENV JIRA_BASE=""
ENV JIRA_EMAIL=""
ENV JIRA_TOKEN=""
ENV OPENAI_API_KEY=""

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

