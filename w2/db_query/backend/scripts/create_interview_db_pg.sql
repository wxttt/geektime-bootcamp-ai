-- Interview Management Database (PostgreSQL Version)
-- 面试管理数据库：记录公司招聘、面试安排、面试结果等信息

DROP DATABASE IF EXISTS interview_db;
CREATE DATABASE interview_db;

\c interview_db

-- 1. 部门表
CREATE TABLE departments (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    code VARCHAR(50) NOT NULL UNIQUE,
    manager_name VARCHAR(100),
    location VARCHAR(100),
    budget DECIMAL(15, 2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. 职位表
CREATE TABLE job_positions (
    id SERIAL PRIMARY KEY,
    department_id INT NOT NULL REFERENCES departments(id),
    title VARCHAR(200) NOT NULL,
    job_code VARCHAR(50) NOT NULL UNIQUE,
    level VARCHAR(50) NOT NULL,
    employment_type VARCHAR(50) NOT NULL,
    min_salary DECIMAL(12, 2),
    max_salary DECIMAL(12, 2),
    headcount INT DEFAULT 1,
    status VARCHAR(50) DEFAULT 'OPEN',
    required_skills TEXT,
    preferred_skills TEXT,
    description TEXT,
    posted_date DATE,
    close_date DATE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 3. 候选人表
CREATE TABLE candidates (
    id SERIAL PRIMARY KEY,
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100) NOT NULL,
    email VARCHAR(200) NOT NULL UNIQUE,
    phone VARCHAR(50),
    current_company VARCHAR(200),
    current_title VARCHAR(200),
    years_of_experience DECIMAL(4, 2),
    education_level VARCHAR(50),
    university VARCHAR(200),
    major VARCHAR(200),
    resume_url VARCHAR(500),
    linkedin_url VARCHAR(500),
    github_url VARCHAR(500),
    source VARCHAR(100),
    status VARCHAR(50) DEFAULT 'ACTIVE',
    location VARCHAR(200),
    expected_salary DECIMAL(12, 2),
    notice_period_days INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 4. 职位申请表
CREATE TABLE applications (
    id SERIAL PRIMARY KEY,
    candidate_id INT NOT NULL REFERENCES candidates(id),
    job_position_id INT NOT NULL REFERENCES job_positions(id),
    application_date DATE NOT NULL,
    status VARCHAR(50) DEFAULT 'PENDING',
    current_stage VARCHAR(100),
    resume_version VARCHAR(50),
    cover_letter TEXT,
    referrer_name VARCHAR(100),
    priority VARCHAR(50) DEFAULT 'MEDIUM',
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (candidate_id, job_position_id)
);

-- 5. 面试官表
CREATE TABLE interviewers (
    id SERIAL PRIMARY KEY,
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100) NOT NULL,
    email VARCHAR(200) NOT NULL UNIQUE,
    department_id INT REFERENCES departments(id),
    title VARCHAR(200),
    expertise TEXT,
    interview_count INT DEFAULT 0,
    avg_rating DECIMAL(3, 2),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 6. 面试轮次配置表
CREATE TABLE interview_rounds (
    id SERIAL PRIMARY KEY,
    job_position_id INT NOT NULL REFERENCES job_positions(id),
    round_number INT NOT NULL,
    round_name VARCHAR(100) NOT NULL,
    round_type VARCHAR(50) NOT NULL,
    duration_minutes INT DEFAULT 60,
    is_required BOOLEAN DEFAULT TRUE,
    description TEXT,
    evaluation_criteria TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (job_position_id, round_number)
);

-- 7. 面试安排表
CREATE TABLE interviews (
    id SERIAL PRIMARY KEY,
    application_id INT NOT NULL REFERENCES applications(id),
    interview_round_id INT NOT NULL REFERENCES interview_rounds(id),
    scheduled_date DATE NOT NULL,
    scheduled_time TIME NOT NULL,
    end_time TIME,
    timezone VARCHAR(50) DEFAULT 'Asia/Shanghai',
    interview_type VARCHAR(50) NOT NULL,
    location VARCHAR(200),
    meeting_link VARCHAR(500),
    status VARCHAR(50) DEFAULT 'SCHEDULED',
    cancellation_reason TEXT,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 8. 面试官分配表
CREATE TABLE interview_assignments (
    id SERIAL PRIMARY KEY,
    interview_id INT NOT NULL REFERENCES interviews(id),
    interviewer_id INT NOT NULL REFERENCES interviewers(id),
    role VARCHAR(50) DEFAULT 'INTERVIEWER',
    confirmed BOOLEAN DEFAULT FALSE,
    feedback_submitted BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (interview_id, interviewer_id)
);

-- 9. 面试反馈表
CREATE TABLE interview_feedback (
    id SERIAL PRIMARY KEY,
    interview_id INT NOT NULL REFERENCES interviews(id),
    interviewer_id INT NOT NULL REFERENCES interviewers(id),
    overall_rating INT CHECK (overall_rating BETWEEN 1 AND 5),
    technical_skills_rating INT CHECK (technical_skills_rating BETWEEN 1 AND 5),
    communication_rating INT CHECK (communication_rating BETWEEN 1 AND 5),
    problem_solving_rating INT CHECK (problem_solving_rating BETWEEN 1 AND 5),
    cultural_fit_rating INT CHECK (cultural_fit_rating BETWEEN 1 AND 5),
    recommendation VARCHAR(50) NOT NULL,
    strengths TEXT,
    weaknesses TEXT,
    detailed_feedback TEXT,
    questions_asked TEXT,
    candidate_questions TEXT,
    submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 10. Offer表
CREATE TABLE offers (
    id SERIAL PRIMARY KEY,
    application_id INT NOT NULL REFERENCES applications(id),
    offer_date DATE NOT NULL,
    expiry_date DATE NOT NULL,
    base_salary DECIMAL(12, 2) NOT NULL,
    bonus DECIMAL(12, 2),
    equity_shares INT,
    sign_on_bonus DECIMAL(12, 2),
    relocation_package DECIMAL(12, 2),
    benefits_package TEXT,
    start_date DATE,
    status VARCHAR(50) DEFAULT 'PENDING',
    response_date DATE,
    rejection_reason TEXT,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 11. 背景调查表
CREATE TABLE background_checks (
    id SERIAL PRIMARY KEY,
    application_id INT NOT NULL REFERENCES applications(id),
    check_type VARCHAR(100) NOT NULL,
    vendor VARCHAR(200),
    initiated_date DATE NOT NULL,
    completed_date DATE,
    status VARCHAR(50) DEFAULT 'PENDING',
    result VARCHAR(50),
    details TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 12. 活动日志表
CREATE TABLE activity_logs (
    id SERIAL PRIMARY KEY,
    application_id INT REFERENCES applications(id),
    interview_id INT REFERENCES interviews(id),
    activity_type VARCHAR(100) NOT NULL,
    actor_name VARCHAR(200),
    actor_email VARCHAR(200),
    description TEXT,
    old_value VARCHAR(500),
    new_value VARCHAR(500),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 创建索引
CREATE INDEX idx_departments_code ON departments(code);
CREATE INDEX idx_job_positions_status ON job_positions(status);
CREATE INDEX idx_job_positions_department ON job_positions(department_id);
CREATE INDEX idx_candidates_email ON candidates(email);
CREATE INDEX idx_candidates_status ON candidates(status);
CREATE INDEX idx_applications_status ON applications(status);
CREATE INDEX idx_applications_date ON applications(application_date);
CREATE INDEX idx_interviewers_email ON interviewers(email);
CREATE INDEX idx_interviews_date ON interviews(scheduled_date);
CREATE INDEX idx_interviews_status ON interviews(status);
CREATE INDEX idx_feedback_rating ON interview_feedback(overall_rating);
CREATE INDEX idx_offers_status ON offers(status);
