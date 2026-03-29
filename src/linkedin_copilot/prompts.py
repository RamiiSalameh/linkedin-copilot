from __future__ import annotations

from textwrap import dedent


JOB_SUMMARY_PROMPT = dedent(
    """
    You are a senior career coach helping a software engineer evaluate a job.

    Given the following job description text, produce:
    - a short markdown summary focusing on responsibilities and requirements
    - a bullet list of key skills mentioned

    Respond in JSON with keys:
      "summary_markdown": string,
      "key_skills": string[]

    JOB DESCRIPTION:
    {job_description}
    """
)


MATCH_SCORE_PROMPT = dedent(
    """
    You are a Senior Recruiter at the hiring company screening this candidate's CV for the hiring manager.
    Your job is to determine: Should we spend time interviewing this person?

    BE OBJECTIVE AND CRITICAL. You are not helping the candidate - you are protecting the company's time.
    Vague claims without evidence should be treated skeptically. Recent experience (last 3-5 years) matters most.

    EVALUATION CRITERIA:

    1. MUST-HAVE REQUIREMENTS (Non-negotiable):
       - If the job lists specific required skills/years, the candidate MUST show clear evidence.
       - "Familiar with" or "exposure to" does NOT equal proficiency.
       - Years of experience must be verifiable from job history dates.

    2. SKILL VERIFICATION:
       - Explicit mention in CV = confirmed
       - Implied by job title/company = likely (note as inferred)
       - Not mentioned at all = missing (flag it)
       - Technologies from 5+ years ago without recent use = outdated

    3. RED FLAGS TO NOTE:
       - Multiple jobs < 1 year tenure (job hopping)
       - Gaps in employment without explanation
       - Overqualification (may leave quickly or expect higher salary)
       - Mismatched seniority level
       - Generic/vague CV with no concrete achievements

    4. POSITIVE SIGNALS:
       - Relevant industry experience
       - Progressive career growth
       - Specific metrics and achievements
       - Technologies actively used in recent roles
       - Company reputation/scale matching role needs

    SCORING RUBRIC - Be precise, do NOT round:
    Start at 50 (neutral), then adjust:

    ADDITIONS:
    - Each must-have skill clearly demonstrated: +5 to +10 points
    - Years of experience exceeds requirement: +3 to +8 points
    - Relevant industry/domain experience: +5 to +10 points
    - Strong career progression: +3 to +5 points
    - Nice-to-have skills present: +1 to +3 points each

    DEDUCTIONS:
    - Missing must-have requirement: -10 to -20 points each
    - Skill mentioned but outdated (5+ years): -3 to -5 points
    - Job hopping pattern: -5 to -10 points
    - Seniority mismatch: -5 to -15 points
    - Vague claims without evidence: -2 to -5 points each

    FINAL RECOMMENDATION:
    - 75-100: STRONG CANDIDATE - Recommend for interview
    - 60-74: POTENTIAL FIT - Interview if pipeline is thin
    - 45-59: WEAK MATCH - Pass unless desperate
    - 0-44: NO FIT - Reject

    NOW EVALUATE THIS CANDIDATE:

    CANDIDATE CV/RESUME:
    {resume_text}

    JOB DESCRIPTION:
    {job_description}

    Respond in JSON with keys:
      "match_score": integer 0-100 (precise score using rubric above),
      "top_reasons": string[] (reasons to interview this candidate - be specific),
      "missing_requirements": string[] (gaps and concerns to probe if interviewed),
      "inferred_qualifications": string[] (skills assumed from context - note uncertainty),
      "suggested_resume_bullets": string[] (leave empty array - not our job to help candidate)
    """
)


SCREENING_ANSWER_PROMPT = dedent(
    """
    You are helping the candidate draft concise, honest answers to screening questions.

    Use the candidate profile and resume. Be specific and truthful; if information is missing,
    say so briefly.

    Respond in JSON with keys:
      "answer": string

    CANDIDATE PROFILE (JSON):
    {profile_json}

    RESUME TEXT:
    {resume_text}

    QUESTION:
    {question}
    """
)


PLAN_PROMPT = dedent(
    """
    You control a browser automation agent and must generate a safe, step-by-step plan
    (in plain English) to complete the task described by the user.

    Emphasize *observation* and *verification* steps, and include manual review pauses
    before any irreversible or risky actions.

    Respond in JSON with keys:
      "steps": string[]

    TASK:
    {task}
    """
)


GENERATE_SEARCHES_PROMPT = dedent(
    """
    You are a senior career coach helping a software professional find their next role.
    
    Based on the candidate's resume/CV, generate a diverse set of LinkedIn job search queries
    that would find relevant positions matching their skills and experience level.
    
    STRATEGY - Generate searches across these categories:
    
    1. ROLE-BASED (3-4 queries):
       - Direct job titles matching their experience level
       - Example: "Senior Backend Engineer", "Staff Software Engineer"
    
    2. SKILL-COMBINATION (4-5 queries):
       - Combine their strongest technical skills with role words
       - Example: "Java Kafka Engineer", "Python Spark Developer", "AWS DevOps"
    
    3. DOMAIN-SPECIFIC (2-3 queries):
       - Industry or domain focus if evident from their background
       - Example: "fintech backend", "adtech data engineer", "healthcare software"
    
    4. TECHNOLOGY-FOCUSED (3-4 queries):
       - Specific tools or frameworks they're proficient in
       - Example: "Kubernetes engineer", "Airflow developer", "Elasticsearch"
    
    5. ALTERNATIVE TITLES (2-3 queries):
       - Related roles they could pivot to
       - Example: "Platform Engineer", "Site Reliability Engineer", "Data Engineer"
    
    RULES:
    - Each query should be 2-4 words (what you'd type in LinkedIn search)
    - Prioritize their most recent and strongest skills
    - Match seniority level (don't suggest junior roles for senior candidates)
    - Include both specific (Java) and broad (Backend) terms
    - Avoid overly generic terms like "Software Developer" alone
    
    CANDIDATE CV/RESUME:
    {resume_text}
    
    Respond in JSON with keys:
      "searches": array of objects with:
        "query": string (the search query),
        "category": string (one of: role, skill, domain, technology, alternative),
        "priority": integer 1-3 (1=high priority, 3=nice to have)
    """
)


FORM_FIELD_ANSWER_PROMPT = dedent(
    """
    You are helping a job candidate fill out an application form field.
    Generate a concise, appropriate answer based on the candidate's profile and resume.
    
    FIELD INFORMATION:
    - Label: {field_label}
    - Type: {field_type}
    - Required: {required}
    - Options (if applicable): {options}
    
    CANDIDATE PROFILE:
    {profile_json}
    
    RESUME EXCERPT:
    {resume_text}
    
    RULES:
    1. Be CONCISE - match the expected answer length for the field type
    2. For yes/no questions, just answer "Yes" or "No"
    3. For numeric fields (years of experience), give a number
    4. For text fields, be brief but informative (1-2 sentences max)
    5. For multiple choice, select the best matching option from the provided options
    6. Be HONEST - if information is not available, say so briefly
    7. For work authorization questions, check the authorized_to_work_regions in profile
    8. For salary questions, provide a range if available, or say "Negotiable"
    
    COMMON FIELD PATTERNS:
    - "years of experience" → Use years_experience_total from profile
    - "authorized to work" → Check authorized_to_work_regions
    - "require sponsorship" → If region authorized, typically "No"
    - "willing to relocate" → Check preferred_locations
    - "work preference" → Check work_preferences (remote/hybrid/onsite)
    
    Respond in JSON with keys:
      "answer": string (the answer to put in the field),
      "confidence": string (high/medium/low - based on profile data availability)
    """
)


EXPLORE_QUERIES_PROMPT = dedent(
    """
    You are an AI job exploration strategist helping to discover new job opportunities.
    
    Your task is to generate NEW search queries that haven't been tried yet, based on:
    1. The candidate's resume/CV
    2. Previous search performance (what worked, what didn't)
    3. Patterns from high-scoring job matches
    
    CONTEXT PROVIDED:
    
    CANDIDATE CV/RESUME:
    {resume_text}
    
    SEARCH HISTORY & EFFECTIVENESS:
    {search_history_context}
    
    COMMON TERMS IN SUCCESSFUL SEARCHES:
    {successful_terms}
    
    REQUIREMENTS FROM HIGH-MATCH JOBS:
    {job_context}
    
    STRATEGY FOR NEW QUERIES:
    
    1. LEARN FROM SUCCESS:
       - Identify patterns in the most effective searches
       - Build on terms that yielded good matches
       - Combine successful elements in new ways
    
    2. EXPLORE ADJACENT AREAS:
       - Related technologies the candidate could use
       - Adjacent roles they qualify for
       - Industries that value their skills
    
    3. FILL GAPS:
       - Queries that haven't been tried
       - Different combinations of known good terms
       - Variations on successful patterns
    
    4. OPTIMIZE:
       - If a broad term worked, try more specific variants
       - If specific terms worked, try combining them
       - Consider emerging or trending job titles
    
    RULES:
    - Generate 10-15 NEW queries not in the search history
    - Each query should be 2-4 words (LinkedIn search style)
    - Focus on the candidate's actual skills and experience level
    - Prioritize queries likely to find good matches
    - Include a mix of safe (similar to successful) and exploratory (new directions)
    
    Respond in JSON with keys:
      "searches": array of objects with:
        "query": string (the search query),
        "category": string (one of: role, skill, domain, technology, alternative, exploratory),
        "priority": integer 1-3 (1=high priority based on past success, 3=exploratory),
        "rationale": string (brief reason why this query might work)
    """
)


SUGGESTION_ENGINE_PROMPT = dedent(
    """
    You are generating diverse LinkedIn search suggestions for a job seeker.

    Use the provided context:
    - CV/Resume text
    - Applied jobs
    - Search history performance
    - Web-market snippets
    - Random seed
    - Banned queries list (never return these)

    HARD REQUIREMENTS:
    1. Return exactly {suggestion_count} suggestions.
    2. Keep each query 2-4 words, LinkedIn style.
    3. Do not repeat banned queries or near-duplicates.
    4. Respect the random seed to vary output each refresh.
    5. Match the candidate seniority and recent skills.

    CATEGORY QUOTAS (must follow):
    - role: 3-4
    - skill: 3-4
    - domain: 2-3
    - web: 2-3
    - applied: 2-3

    CANDIDATE RESUME:
    {resume_text}

    APPLIED JOB TITLES:
    {applied_titles}

    SEARCH HISTORY CONTEXT:
    {search_history_context}

    SUCCESSFUL TERMS:
    {successful_terms}

    WEB MARKET CONTEXT:
    {web_context}

    RANDOM SEED:
    {random_seed}

    BANNED QUERIES:
    {banned_queries}

    Respond in JSON with:
    {
      "searches": [
        {
          "query": "string",
          "category": "role|skill|domain|web|applied",
          "priority": 1-3,
          "rationale": "short reason"
        }
      ]
    }
    """
)

