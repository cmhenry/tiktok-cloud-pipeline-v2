--
-- PostgreSQL database dump
--

\restrict ng9o0VxyaUsgEwhKEvV2UGBcrNCtAZ6lpg8n74j2ThpMXRRbUEfB3bwPLD9crPs

-- Dumped from database version 16.11 (Ubuntu 16.11-0ubuntu0.24.04.1)
-- Dumped by pg_dump version 16.11 (Ubuntu 16.11-0ubuntu0.24.04.1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_table_access_method = heap;

--
-- Name: backlog; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.backlog (
    backlog_id integer NOT NULL,
    meta_id character varying(255),
    first_report_date date NOT NULL,
    first_report_device_id integer,
    report_count integer DEFAULT 1,
    is_active boolean DEFAULT true,
    added_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    completed_at timestamp without time zone,
    removal_reason character varying(20),
    last_report_date date NOT NULL,
    report_details jsonb DEFAULT '[]'::jsonb
);


ALTER TABLE public.backlog OWNER TO postgres;

--
-- Name: TABLE backlog; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.backlog IS 'Posts requiring re-reports from different devices in same region';


--
-- Name: COLUMN backlog.meta_id; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.backlog.meta_id IS 'Foreign key to transcripts table';


--
-- Name: COLUMN backlog.first_report_date; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.backlog.first_report_date IS 'Date when the first report was submitted';


--
-- Name: COLUMN backlog.first_report_device_id; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.backlog.first_report_device_id IS 'Device used for the first report';


--
-- Name: COLUMN backlog.report_count; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.backlog.report_count IS 'Current count of reports; post exits backlog at 3';


--
-- Name: COLUMN backlog.is_active; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.backlog.is_active IS 'Whether post still needs re-reports';


--
-- Name: COLUMN backlog.completed_at; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.backlog.completed_at IS 'When post exited the backlog';


--
-- Name: COLUMN backlog.removal_reason; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.backlog.removal_reason IS 'Why post left backlog: completed (3 reports), takedown, or manual removal';


--
-- Name: COLUMN backlog.last_report_date; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.backlog.last_report_date IS 'Date of most recent report; used to enforce 1 re-report per day';


--
-- Name: COLUMN backlog.report_details; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.backlog.report_details IS 'Array of report submission details (category, target, severity, language) for each report';


--
-- Name: backlog_backlog_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.backlog_backlog_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.backlog_backlog_id_seq OWNER TO postgres;

--
-- Name: backlog_backlog_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.backlog_backlog_id_seq OWNED BY public.backlog.backlog_id;


--
-- Name: creators; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.creators (
    creator_id integer NOT NULL,
    author_uniqueid character varying(100) NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


ALTER TABLE public.creators OWNER TO postgres;

--
-- Name: TABLE creators; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.creators IS 'Minimal creators table for enabling future creator-level features and tracking';


--
-- Name: COLUMN creators.author_uniqueid; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.creators.author_uniqueid IS 'TikTok author unique identifier (username)';


--
-- Name: creators_creator_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.creators_creator_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.creators_creator_id_seq OWNER TO postgres;

--
-- Name: creators_creator_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.creators_creator_id_seq OWNED BY public.creators.creator_id;


--
-- Name: daily_randomization; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.daily_randomization (
    randomization_date date NOT NULL,
    report_region character varying(10) NOT NULL,
    last_treatment character varying(20),
    pair_complete boolean DEFAULT true,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now(),
    CONSTRAINT daily_randomization_last_treatment_check CHECK (((last_treatment)::text = ANY ((ARRAY['treatment'::character varying, 'control'::character varying])::text[]))),
    CONSTRAINT daily_randomization_report_region_check CHECK (((report_region)::text = ANY ((ARRAY['US'::character varying, 'EU'::character varying, 'LATAM'::character varying])::text[])))
);


ALTER TABLE public.daily_randomization OWNER TO postgres;

--
-- Name: TABLE daily_randomization; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.daily_randomization IS 'Tracks daily randomization state for treatment pairing (alternating treatment/control) and day-level region assignment';


--
-- Name: COLUMN daily_randomization.report_region; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.daily_randomization.report_region IS 'Region for all posts assigned treatment this day: US (50%), EU (25%), or LATAM (25%)';


--
-- Name: COLUMN daily_randomization.last_treatment; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.daily_randomization.last_treatment IS 'The treatment assignment of the last post in the current pair';


--
-- Name: COLUMN daily_randomization.pair_complete; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.daily_randomization.pair_complete IS 'TRUE = next assignment starts a new pair (randomize), FALSE = next assignment completes pair (assign opposite)';


--
-- Name: devices; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.devices (
    device_id integer NOT NULL,
    device_label character varying(50) NOT NULL,
    region character varying(10) NOT NULL,
    is_active boolean DEFAULT true,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    country character varying(10)
);


ALTER TABLE public.devices OWNER TO postgres;

--
-- Name: TABLE devices; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.devices IS 'Physical devices (phones) used for reporting TikTok content';


--
-- Name: COLUMN devices.device_label; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.devices.device_label IS 'Unique identifier label for the device (e.g., US-1, UK-2)';


--
-- Name: COLUMN devices.region; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.devices.region IS 'Study region derived from country (US, EU, LATAM)';


--
-- Name: COLUMN devices.is_active; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.devices.is_active IS 'Whether device is currently available for use';


--
-- Name: COLUMN devices.country; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.devices.country IS 'Country code where device is registered (US, UK, ES, EC, MX, CO, AR, PE)';


--
-- Name: devices_device_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.devices_device_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.devices_device_id_seq OWNER TO postgres;

--
-- Name: devices_device_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.devices_device_id_seq OWNED BY public.devices.device_id;


--
-- Name: experiment_config; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.experiment_config (
    config_id integer NOT NULL,
    config_name character varying(100) NOT NULL,
    queue_size_per_ra integer DEFAULT 25,
    label_threshold numeric(10,9) DEFAULT 0.70,
    treatment_split numeric(3,2) DEFAULT 0.50,
    stratify_by_country boolean DEFAULT true,
    stratify_by_language boolean DEFAULT true,
    min_items_per_stratum integer DEFAULT 3,
    is_active boolean DEFAULT false,
    notes text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    created_by_user_id integer,
    CONSTRAINT experiment_config_label_threshold_check CHECK (((label_threshold >= (0)::numeric) AND (label_threshold <= (1)::numeric))),
    CONSTRAINT experiment_config_min_items_per_stratum_check CHECK ((min_items_per_stratum >= 0)),
    CONSTRAINT experiment_config_queue_size_per_ra_check CHECK ((queue_size_per_ra > 0)),
    CONSTRAINT experiment_config_treatment_split_check CHECK (((treatment_split >= (0)::numeric) AND (treatment_split <= (1)::numeric)))
);


ALTER TABLE public.experiment_config OWNER TO postgres;

--
-- Name: TABLE experiment_config; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.experiment_config IS 'Admin-configurable experiment parameters for queue generation';


--
-- Name: COLUMN experiment_config.queue_size_per_ra; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.experiment_config.queue_size_per_ra IS 'Number of items to assign to each research assistant per queue generation';


--
-- Name: COLUMN experiment_config.label_threshold; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.experiment_config.label_threshold IS 'Minimum label_1_pred probability score to include content (0.0 to 1.0)';


--
-- Name: COLUMN experiment_config.treatment_split; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.experiment_config.treatment_split IS 'Proportion assigned to treatment group (e.g., 0.50 = 50% treatment, 50% control)';


--
-- Name: COLUMN experiment_config.stratify_by_country; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.experiment_config.stratify_by_country IS 'Whether to stratify sampling by country';


--
-- Name: COLUMN experiment_config.stratify_by_language; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.experiment_config.stratify_by_language IS 'Whether to stratify sampling by language';


--
-- Name: COLUMN experiment_config.min_items_per_stratum; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.experiment_config.min_items_per_stratum IS 'Minimum items to sample from each country/language combination';


--
-- Name: COLUMN experiment_config.is_active; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.experiment_config.is_active IS 'Only one config can be active at a time (enforced by unique index)';


--
-- Name: experiment_config_config_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.experiment_config_config_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.experiment_config_config_id_seq OWNER TO postgres;

--
-- Name: experiment_config_config_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.experiment_config_config_id_seq OWNED BY public.experiment_config.config_id;


--
-- Name: queue_assignments; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.queue_assignments (
    assignment_id integer NOT NULL,
    meta_id character varying(255),
    user_id integer NOT NULL,
    treatment_round integer,
    treatment_group character varying(50),
    status character varying(20) DEFAULT 'pending'::character varying,
    assigned_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    completed_at timestamp without time zone,
    batch_id integer,
    stratum_info jsonb,
    label_1_pred numeric(10,9),
    assigned_treatment boolean,
    assignment_type character varying(20) DEFAULT 'new_post'::character varying,
    device_id integer,
    locked_at timestamp without time zone,
    lock_expires_at timestamp without time zone,
    last_language character varying(10),
    CONSTRAINT queue_assignments_status_check CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'in_progress'::character varying, 'completed'::character varying, 'skipped'::character varying])::text[]))),
    CONSTRAINT queue_assignments_treatment_round_check CHECK (((treatment_round IS NULL) OR (treatment_round = ANY (ARRAY[1, 2]))))
);


ALTER TABLE public.queue_assignments OWNER TO postgres;

--
-- Name: COLUMN queue_assignments.batch_id; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.queue_assignments.batch_id IS 'Foreign key to queue_batches - which batch this assignment belongs to';


--
-- Name: COLUMN queue_assignments.stratum_info; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.queue_assignments.stratum_info IS 'JSON metadata for stratification (e.g., {"country": "US", "lang": "en"})';


--
-- Name: COLUMN queue_assignments.label_1_pred; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.queue_assignments.label_1_pred IS 'Copy of probability score from transcripts at assignment time';


--
-- Name: COLUMN queue_assignments.assigned_treatment; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.queue_assignments.assigned_treatment IS 'Treatment assignment: TRUE for treatment group, FALSE for control group';


--
-- Name: COLUMN queue_assignments.assignment_type; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.queue_assignments.assignment_type IS 'Type: backlog, new_post, or second_review';


--
-- Name: COLUMN queue_assignments.device_id; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.queue_assignments.device_id IS 'Required device for backlog tasks; NULL for new_post';


--
-- Name: COLUMN queue_assignments.locked_at; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.queue_assignments.locked_at IS 'When RA started working on this task';


--
-- Name: COLUMN queue_assignments.lock_expires_at; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.queue_assignments.lock_expires_at IS 'Lock auto-releases after this time (15 min default)';


--
-- Name: COLUMN queue_assignments.last_language; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.queue_assignments.last_language IS 'Language of this assignment for EN/ES alternation';


--
-- Name: queue_assignments_assignment_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.queue_assignments_assignment_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.queue_assignments_assignment_id_seq OWNER TO postgres;

--
-- Name: queue_assignments_assignment_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.queue_assignments_assignment_id_seq OWNED BY public.queue_assignments.assignment_id;


--
-- Name: queue_batches; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.queue_batches (
    batch_id integer NOT NULL,
    generation_date date NOT NULL,
    generation_timestamp timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    config_id integer,
    total_items_generated integer,
    items_per_ra jsonb,
    status character varying(20) DEFAULT 'generated'::character varying,
    error_log text,
    generated_by_user_id integer,
    CONSTRAINT queue_batches_status_check CHECK (((status)::text = ANY ((ARRAY['generated'::character varying, 'active'::character varying, 'completed'::character varying, 'failed'::character varying, 'cancelled'::character varying])::text[])))
);


ALTER TABLE public.queue_batches OWNER TO postgres;

--
-- Name: TABLE queue_batches; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.queue_batches IS 'Tracks each queue generation event with metadata and status';


--
-- Name: COLUMN queue_batches.generation_date; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.queue_batches.generation_date IS 'The date this queue is for (e.g., 2024-10-23)';


--
-- Name: COLUMN queue_batches.generation_timestamp; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.queue_batches.generation_timestamp IS 'When the queue was actually generated';


--
-- Name: COLUMN queue_batches.items_per_ra; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.queue_batches.items_per_ra IS 'JSON object with user_id: count mapping (e.g., {"1": 25, "2": 25})';


--
-- Name: COLUMN queue_batches.status; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.queue_batches.status IS 'Batch lifecycle: generated, active, completed, failed, cancelled';


--
-- Name: COLUMN queue_batches.error_log; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.queue_batches.error_log IS 'Error details if generation failed';


--
-- Name: COLUMN queue_batches.generated_by_user_id; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.queue_batches.generated_by_user_id IS 'Admin who triggered generation (NULL if automated)';


--
-- Name: queue_batches_batch_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.queue_batches_batch_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.queue_batches_batch_id_seq OWNER TO postgres;

--
-- Name: queue_batches_batch_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.queue_batches_batch_id_seq OWNED BY public.queue_batches.batch_id;


--
-- Name: ra_availability; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.ra_availability (
    availability_id integer NOT NULL,
    ra_id integer,
    work_date date NOT NULL,
    expected_hours numeric(4,2) DEFAULT 0,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_by integer
);


ALTER TABLE public.ra_availability OWNER TO postgres;

--
-- Name: TABLE ra_availability; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.ra_availability IS 'Daily availability hours for RAs, uploaded by supervisors';


--
-- Name: COLUMN ra_availability.ra_id; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.ra_availability.ra_id IS 'Foreign key to users table (RA user_id)';


--
-- Name: COLUMN ra_availability.work_date; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.ra_availability.work_date IS 'Date for which availability is set';


--
-- Name: COLUMN ra_availability.expected_hours; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.ra_availability.expected_hours IS 'Expected working hours for this RA on this date';


--
-- Name: COLUMN ra_availability.updated_by; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.ra_availability.updated_by IS 'User who last updated this availability record';


--
-- Name: ra_availability_availability_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.ra_availability_availability_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.ra_availability_availability_id_seq OWNER TO postgres;

--
-- Name: ra_availability_availability_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.ra_availability_availability_id_seq OWNED BY public.ra_availability.availability_id;


--
-- Name: ra_devices; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.ra_devices (
    ra_id integer NOT NULL,
    device_id integer NOT NULL,
    assigned_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


ALTER TABLE public.ra_devices OWNER TO postgres;

--
-- Name: TABLE ra_devices; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.ra_devices IS 'Junction table mapping RAs to their assigned devices';


--
-- Name: COLUMN ra_devices.ra_id; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.ra_devices.ra_id IS 'Foreign key to users table (RA user_id)';


--
-- Name: COLUMN ra_devices.device_id; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.ra_devices.device_id IS 'Foreign key to devices table';


--
-- Name: COLUMN ra_devices.assigned_at; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.ra_devices.assigned_at IS 'When the device was assigned to this RA';


--
-- Name: second_review_queue; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.second_review_queue (
    review_id integer NOT NULL,
    meta_id character varying(255),
    first_reviewer_id integer,
    first_review_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    second_reviewer_id integer,
    assigned_at timestamp without time zone,
    completed_at timestamp without time zone,
    status character varying(20) DEFAULT 'pending'::character varying
);


ALTER TABLE public.second_review_queue OWNER TO postgres;

--
-- Name: TABLE second_review_queue; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.second_review_queue IS 'Posts marked reportable with low confidence, awaiting second RA review';


--
-- Name: COLUMN second_review_queue.meta_id; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.second_review_queue.meta_id IS 'Foreign key to transcripts table';


--
-- Name: COLUMN second_review_queue.first_reviewer_id; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.second_review_queue.first_reviewer_id IS 'RA who made the initial low-confidence decision';


--
-- Name: COLUMN second_review_queue.first_review_at; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.second_review_queue.first_review_at IS 'When the first review was submitted';


--
-- Name: COLUMN second_review_queue.second_reviewer_id; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.second_review_queue.second_reviewer_id IS 'RA assigned to perform second review';


--
-- Name: COLUMN second_review_queue.assigned_at; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.second_review_queue.assigned_at IS 'When second reviewer was assigned';


--
-- Name: COLUMN second_review_queue.completed_at; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.second_review_queue.completed_at IS 'When second review was completed';


--
-- Name: COLUMN second_review_queue.status; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.second_review_queue.status IS 'Lifecycle status: pending, assigned, completed';


--
-- Name: second_review_queue_review_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.second_review_queue_review_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.second_review_queue_review_id_seq OWNER TO postgres;

--
-- Name: second_review_queue_review_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.second_review_queue_review_id_seq OWNED BY public.second_review_queue.review_id;


--
-- Name: transcripts; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.transcripts (
    meta_id character varying(255) NOT NULL,
    year integer NOT NULL,
    month integer NOT NULL,
    day integer NOT NULL,
    transcript text NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    video_url text,
    creator_id integer,
    label_1_pred numeric(10,9),
    country character varying(50),
    lang character varying(20),
    queue_status character varying(20) DEFAULT 'unqueued'::character varying,
    first_report_date date,
    first_report_device_id integer,
    total_report_count integer DEFAULT 0,
    review_status character varying(30) DEFAULT 'unreviewed'::character varying,
    is_reportable boolean,
    confidence_level character varying(10),
    in_study boolean DEFAULT false,
    treatment_assignment character varying(10),
    report_region character varying(10),
    CONSTRAINT chk_transcripts_queue_status CHECK (((queue_status)::text = ANY ((ARRAY['unqueued'::character varying, 'queued'::character varying, 'in_progress'::character varying, 'completed'::character varying, 'excluded'::character varying])::text[]))),
    CONSTRAINT transcripts_day_check CHECK (((day >= 1) AND (day <= 31))),
    CONSTRAINT transcripts_month_check CHECK (((month >= 1) AND (month <= 12)))
);


ALTER TABLE public.transcripts OWNER TO postgres;

--
-- Name: COLUMN transcripts.video_url; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.transcripts.video_url IS 'Full TikTok video URL for viewing the content';


--
-- Name: COLUMN transcripts.creator_id; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.transcripts.creator_id IS 'Foreign key to creators table';


--
-- Name: COLUMN transcripts.label_1_pred; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.transcripts.label_1_pred IS 'Classifier probability score (0.0 to 1.0) for content of interest';


--
-- Name: COLUMN transcripts.country; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.transcripts.country IS 'Content country code';


--
-- Name: COLUMN transcripts.lang; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.transcripts.lang IS 'Content language code (ISO 639-1)';


--
-- Name: COLUMN transcripts.queue_status; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.transcripts.queue_status IS 'Lifecycle status: unqueued, queued, in_progress, completed, excluded';


--
-- Name: COLUMN transcripts.first_report_date; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.transcripts.first_report_date IS 'Date when the first report was submitted for this post';


--
-- Name: COLUMN transcripts.first_report_device_id; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.transcripts.first_report_device_id IS 'Device used for the first report';


--
-- Name: COLUMN transcripts.total_report_count; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.transcripts.total_report_count IS 'Total number of reports submitted for this post';


--
-- Name: COLUMN transcripts.review_status; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.transcripts.review_status IS 'RA review lifecycle: unreviewed, in_review, reviewed, second_review_pending, second_review_complete';


--
-- Name: COLUMN transcripts.is_reportable; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.transcripts.is_reportable IS 'Whether RA determined post should be reported';


--
-- Name: COLUMN transcripts.confidence_level; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.transcripts.confidence_level IS 'RA confidence in reportability decision: high or low';


--
-- Name: COLUMN transcripts.in_study; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.transcripts.in_study IS 'TRUE if post passed reportability logic and is in experiment';


--
-- Name: COLUMN transcripts.treatment_assignment; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.transcripts.treatment_assignment IS 'Canonical treatment/control assignment for posts in study';


--
-- Name: COLUMN transcripts.report_region; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.transcripts.report_region IS 'Assigned region for reporting: 50% US, 25% EU, 25% LATAM';


--
-- Name: user_classifications; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.user_classifications (
    classification_id integer NOT NULL,
    meta_id character varying(255),
    user_id integer NOT NULL,
    treatment_round integer NOT NULL,
    classification_data jsonb,
    notes text,
    classified_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    confidence_level character varying(10),
    CONSTRAINT user_classifications_treatment_round_check CHECK ((treatment_round = ANY (ARRAY[1, 2])))
);


ALTER TABLE public.user_classifications OWNER TO postgres;

--
-- Name: COLUMN user_classifications.confidence_level; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.user_classifications.confidence_level IS 'RA confidence: high or low';


--
-- Name: user_classifications_classification_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.user_classifications_classification_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.user_classifications_classification_id_seq OWNER TO postgres;

--
-- Name: user_classifications_classification_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.user_classifications_classification_id_seq OWNED BY public.user_classifications.classification_id;


--
-- Name: users; Type: TABLE; Schema: public; Owner: transcript_user
--

CREATE TABLE public.users (
    user_id integer NOT NULL,
    username character varying(50) NOT NULL,
    password_hash character varying(255) NOT NULL,
    email character varying(255),
    full_name character varying(100),
    role character varying(20) DEFAULT 'researcher'::character varying,
    is_active boolean DEFAULT true,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    last_login timestamp without time zone,
    CONSTRAINT users_role_check CHECK (((role)::text = ANY ((ARRAY['admin'::character varying, 'researcher'::character varying, 'supervisor'::character varying])::text[])))
);


ALTER TABLE public.users OWNER TO transcript_user;

--
-- Name: TABLE users; Type: COMMENT; Schema: public; Owner: transcript_user
--

COMMENT ON TABLE public.users IS 'Stores user accounts for the content classification system';


--
-- Name: COLUMN users.role; Type: COMMENT; Schema: public; Owner: transcript_user
--

COMMENT ON COLUMN public.users.role IS 'User role: admin (full access), researcher (classification), supervisor (view only)';


--
-- Name: COLUMN users.is_active; Type: COMMENT; Schema: public; Owner: transcript_user
--

COMMENT ON COLUMN public.users.is_active IS 'Whether the user account is active and can log in';


--
-- Name: users_user_id_seq; Type: SEQUENCE; Schema: public; Owner: transcript_user
--

CREATE SEQUENCE public.users_user_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.users_user_id_seq OWNER TO transcript_user;

--
-- Name: users_user_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: transcript_user
--

ALTER SEQUENCE public.users_user_id_seq OWNED BY public.users.user_id;


--
-- Name: backlog backlog_id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.backlog ALTER COLUMN backlog_id SET DEFAULT nextval('public.backlog_backlog_id_seq'::regclass);


--
-- Name: creators creator_id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.creators ALTER COLUMN creator_id SET DEFAULT nextval('public.creators_creator_id_seq'::regclass);


--
-- Name: devices device_id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.devices ALTER COLUMN device_id SET DEFAULT nextval('public.devices_device_id_seq'::regclass);


--
-- Name: experiment_config config_id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.experiment_config ALTER COLUMN config_id SET DEFAULT nextval('public.experiment_config_config_id_seq'::regclass);


--
-- Name: queue_assignments assignment_id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.queue_assignments ALTER COLUMN assignment_id SET DEFAULT nextval('public.queue_assignments_assignment_id_seq'::regclass);


--
-- Name: queue_batches batch_id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.queue_batches ALTER COLUMN batch_id SET DEFAULT nextval('public.queue_batches_batch_id_seq'::regclass);


--
-- Name: ra_availability availability_id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.ra_availability ALTER COLUMN availability_id SET DEFAULT nextval('public.ra_availability_availability_id_seq'::regclass);


--
-- Name: second_review_queue review_id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.second_review_queue ALTER COLUMN review_id SET DEFAULT nextval('public.second_review_queue_review_id_seq'::regclass);


--
-- Name: user_classifications classification_id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.user_classifications ALTER COLUMN classification_id SET DEFAULT nextval('public.user_classifications_classification_id_seq'::regclass);


--
-- Name: users user_id; Type: DEFAULT; Schema: public; Owner: transcript_user
--

ALTER TABLE ONLY public.users ALTER COLUMN user_id SET DEFAULT nextval('public.users_user_id_seq'::regclass);


--
-- Name: backlog backlog_meta_id_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.backlog
    ADD CONSTRAINT backlog_meta_id_key UNIQUE (meta_id);


--
-- Name: backlog backlog_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.backlog
    ADD CONSTRAINT backlog_pkey PRIMARY KEY (backlog_id);


--
-- Name: creators creators_author_uniqueid_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.creators
    ADD CONSTRAINT creators_author_uniqueid_key UNIQUE (author_uniqueid);


--
-- Name: creators creators_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.creators
    ADD CONSTRAINT creators_pkey PRIMARY KEY (creator_id);


--
-- Name: daily_randomization daily_randomization_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.daily_randomization
    ADD CONSTRAINT daily_randomization_pkey PRIMARY KEY (randomization_date);


--
-- Name: devices devices_device_label_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.devices
    ADD CONSTRAINT devices_device_label_key UNIQUE (device_label);


--
-- Name: devices devices_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.devices
    ADD CONSTRAINT devices_pkey PRIMARY KEY (device_id);


--
-- Name: experiment_config experiment_config_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.experiment_config
    ADD CONSTRAINT experiment_config_pkey PRIMARY KEY (config_id);


--
-- Name: queue_assignments queue_assignments_meta_id_treatment_round_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.queue_assignments
    ADD CONSTRAINT queue_assignments_meta_id_treatment_round_key UNIQUE (meta_id, treatment_round);


--
-- Name: queue_assignments queue_assignments_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.queue_assignments
    ADD CONSTRAINT queue_assignments_pkey PRIMARY KEY (assignment_id);


--
-- Name: queue_batches queue_batches_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.queue_batches
    ADD CONSTRAINT queue_batches_pkey PRIMARY KEY (batch_id);


--
-- Name: ra_availability ra_availability_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.ra_availability
    ADD CONSTRAINT ra_availability_pkey PRIMARY KEY (availability_id);


--
-- Name: ra_availability ra_availability_ra_id_work_date_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.ra_availability
    ADD CONSTRAINT ra_availability_ra_id_work_date_key UNIQUE (ra_id, work_date);


--
-- Name: ra_devices ra_devices_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.ra_devices
    ADD CONSTRAINT ra_devices_pkey PRIMARY KEY (ra_id, device_id);


--
-- Name: second_review_queue second_review_queue_meta_id_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.second_review_queue
    ADD CONSTRAINT second_review_queue_meta_id_key UNIQUE (meta_id);


--
-- Name: second_review_queue second_review_queue_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.second_review_queue
    ADD CONSTRAINT second_review_queue_pkey PRIMARY KEY (review_id);


--
-- Name: transcripts transcripts_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.transcripts
    ADD CONSTRAINT transcripts_pkey PRIMARY KEY (meta_id);


--
-- Name: user_classifications user_classifications_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.user_classifications
    ADD CONSTRAINT user_classifications_pkey PRIMARY KEY (classification_id);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: transcript_user
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: transcript_user
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (user_id);


--
-- Name: users users_username_key; Type: CONSTRAINT; Schema: public; Owner: transcript_user
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_username_key UNIQUE (username);


--
-- Name: idx_assignments_batch; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_assignments_batch ON public.queue_assignments USING btree (batch_id);


--
-- Name: idx_assignments_device; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_assignments_device ON public.queue_assignments USING btree (device_id);


--
-- Name: idx_assignments_lock_expires; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_assignments_lock_expires ON public.queue_assignments USING btree (lock_expires_at) WHERE (lock_expires_at IS NOT NULL);


--
-- Name: idx_assignments_treatment; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_assignments_treatment ON public.queue_assignments USING btree (assigned_treatment);


--
-- Name: idx_assignments_type; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_assignments_type ON public.queue_assignments USING btree (assignment_type);


--
-- Name: idx_backlog_active; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_backlog_active ON public.backlog USING btree (is_active) WHERE (is_active = true);


--
-- Name: idx_backlog_first_report_date; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_backlog_first_report_date ON public.backlog USING btree (first_report_date);


--
-- Name: idx_backlog_last_report_date; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_backlog_last_report_date ON public.backlog USING btree (last_report_date);


--
-- Name: idx_batches_date; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_batches_date ON public.queue_batches USING btree (generation_date);


--
-- Name: idx_batches_status; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_batches_status ON public.queue_batches USING btree (status);


--
-- Name: idx_classification_meta; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_classification_meta ON public.user_classifications USING btree (meta_id, treatment_round);


--
-- Name: idx_creators_author_uniqueid; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_creators_author_uniqueid ON public.creators USING btree (author_uniqueid);


--
-- Name: idx_daily_randomization_date; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_daily_randomization_date ON public.daily_randomization USING btree (randomization_date);


--
-- Name: idx_devices_country; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_devices_country ON public.devices USING btree (country);


--
-- Name: idx_devices_region; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_devices_region ON public.devices USING btree (region);


--
-- Name: idx_one_active_config; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX idx_one_active_config ON public.experiment_config USING btree (is_active) WHERE (is_active = true);


--
-- Name: idx_queue_status; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_queue_status ON public.queue_assignments USING btree (status, treatment_round);


--
-- Name: idx_queue_user; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_queue_user ON public.queue_assignments USING btree (user_id, status);


--
-- Name: idx_ra_availability_date; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_ra_availability_date ON public.ra_availability USING btree (work_date);


--
-- Name: idx_ra_availability_ra_date; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_ra_availability_ra_date ON public.ra_availability USING btree (ra_id, work_date);


--
-- Name: idx_ra_devices_device; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_ra_devices_device ON public.ra_devices USING btree (device_id);


--
-- Name: idx_ra_devices_ra; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_ra_devices_ra ON public.ra_devices USING btree (ra_id);


--
-- Name: idx_second_review_pending; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_second_review_pending ON public.second_review_queue USING btree (status) WHERE ((status)::text = 'pending'::text);


--
-- Name: idx_second_review_status; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_second_review_status ON public.second_review_queue USING btree (status);


--
-- Name: idx_transcript_date; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_transcript_date ON public.transcripts USING btree (year, month, day);


--
-- Name: idx_transcripts_country_lang; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_transcripts_country_lang ON public.transcripts USING btree (country, lang);


--
-- Name: idx_transcripts_creator_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_transcripts_creator_id ON public.transcripts USING btree (creator_id);


--
-- Name: idx_transcripts_in_study; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_transcripts_in_study ON public.transcripts USING btree (in_study) WHERE (in_study = true);


--
-- Name: idx_transcripts_label_pred; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_transcripts_label_pred ON public.transcripts USING btree (label_1_pred);


--
-- Name: idx_transcripts_queue_status; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_transcripts_queue_status ON public.transcripts USING btree (queue_status);


--
-- Name: idx_transcripts_report_region; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_transcripts_report_region ON public.transcripts USING btree (report_region);


--
-- Name: idx_transcripts_review_status; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_transcripts_review_status ON public.transcripts USING btree (review_status);


--
-- Name: idx_users_username; Type: INDEX; Schema: public; Owner: transcript_user
--

CREATE INDEX idx_users_username ON public.users USING btree (username);


--
-- Name: backlog backlog_first_report_device_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.backlog
    ADD CONSTRAINT backlog_first_report_device_id_fkey FOREIGN KEY (first_report_device_id) REFERENCES public.devices(device_id);


--
-- Name: backlog backlog_meta_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.backlog
    ADD CONSTRAINT backlog_meta_id_fkey FOREIGN KEY (meta_id) REFERENCES public.transcripts(meta_id) ON DELETE CASCADE;


--
-- Name: experiment_config experiment_config_created_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.experiment_config
    ADD CONSTRAINT experiment_config_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(user_id);


--
-- Name: queue_assignments fk_queue_assignments_batch; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.queue_assignments
    ADD CONSTRAINT fk_queue_assignments_batch FOREIGN KEY (batch_id) REFERENCES public.queue_batches(batch_id) ON DELETE CASCADE;


--
-- Name: queue_assignments fk_queue_assignments_user; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.queue_assignments
    ADD CONSTRAINT fk_queue_assignments_user FOREIGN KEY (user_id) REFERENCES public.users(user_id) ON DELETE RESTRICT;


--
-- Name: CONSTRAINT fk_queue_assignments_user ON queue_assignments; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON CONSTRAINT fk_queue_assignments_user ON public.queue_assignments IS 'Ensures queue_assignments.user_id references a valid user. ON DELETE RESTRICT prevents deletion of users with assignments.';


--
-- Name: transcripts fk_transcripts_creator; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.transcripts
    ADD CONSTRAINT fk_transcripts_creator FOREIGN KEY (creator_id) REFERENCES public.creators(creator_id) ON DELETE SET NULL;


--
-- Name: user_classifications fk_user_classifications_user; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.user_classifications
    ADD CONSTRAINT fk_user_classifications_user FOREIGN KEY (user_id) REFERENCES public.users(user_id) ON DELETE RESTRICT;


--
-- Name: CONSTRAINT fk_user_classifications_user ON user_classifications; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON CONSTRAINT fk_user_classifications_user ON public.user_classifications IS 'Ensures user_classifications.user_id references a valid user. ON DELETE RESTRICT prevents deletion of users with classifications.';


--
-- Name: queue_assignments queue_assignments_device_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.queue_assignments
    ADD CONSTRAINT queue_assignments_device_id_fkey FOREIGN KEY (device_id) REFERENCES public.devices(device_id);


--
-- Name: queue_assignments queue_assignments_meta_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.queue_assignments
    ADD CONSTRAINT queue_assignments_meta_id_fkey FOREIGN KEY (meta_id) REFERENCES public.transcripts(meta_id) ON DELETE CASCADE;


--
-- Name: queue_batches queue_batches_config_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.queue_batches
    ADD CONSTRAINT queue_batches_config_id_fkey FOREIGN KEY (config_id) REFERENCES public.experiment_config(config_id);


--
-- Name: queue_batches queue_batches_generated_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.queue_batches
    ADD CONSTRAINT queue_batches_generated_by_user_id_fkey FOREIGN KEY (generated_by_user_id) REFERENCES public.users(user_id);


--
-- Name: ra_availability ra_availability_ra_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.ra_availability
    ADD CONSTRAINT ra_availability_ra_id_fkey FOREIGN KEY (ra_id) REFERENCES public.users(user_id) ON DELETE CASCADE;


--
-- Name: ra_availability ra_availability_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.ra_availability
    ADD CONSTRAINT ra_availability_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.users(user_id);


--
-- Name: ra_devices ra_devices_device_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.ra_devices
    ADD CONSTRAINT ra_devices_device_id_fkey FOREIGN KEY (device_id) REFERENCES public.devices(device_id) ON DELETE CASCADE;


--
-- Name: ra_devices ra_devices_ra_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.ra_devices
    ADD CONSTRAINT ra_devices_ra_id_fkey FOREIGN KEY (ra_id) REFERENCES public.users(user_id) ON DELETE CASCADE;


--
-- Name: second_review_queue second_review_queue_first_reviewer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.second_review_queue
    ADD CONSTRAINT second_review_queue_first_reviewer_id_fkey FOREIGN KEY (first_reviewer_id) REFERENCES public.users(user_id);


--
-- Name: second_review_queue second_review_queue_meta_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.second_review_queue
    ADD CONSTRAINT second_review_queue_meta_id_fkey FOREIGN KEY (meta_id) REFERENCES public.transcripts(meta_id) ON DELETE CASCADE;


--
-- Name: second_review_queue second_review_queue_second_reviewer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.second_review_queue
    ADD CONSTRAINT second_review_queue_second_reviewer_id_fkey FOREIGN KEY (second_reviewer_id) REFERENCES public.users(user_id);


--
-- Name: transcripts transcripts_first_report_device_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.transcripts
    ADD CONSTRAINT transcripts_first_report_device_id_fkey FOREIGN KEY (first_report_device_id) REFERENCES public.devices(device_id);


--
-- Name: user_classifications user_classifications_meta_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.user_classifications
    ADD CONSTRAINT user_classifications_meta_id_fkey FOREIGN KEY (meta_id) REFERENCES public.transcripts(meta_id) ON DELETE CASCADE;


--
-- Name: TABLE backlog; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.backlog TO transcript_user;


--
-- Name: SEQUENCE backlog_backlog_id_seq; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT,USAGE ON SEQUENCE public.backlog_backlog_id_seq TO transcript_user;


--
-- Name: TABLE creators; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.creators TO transcript_user;


--
-- Name: SEQUENCE creators_creator_id_seq; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON SEQUENCE public.creators_creator_id_seq TO transcript_user;


--
-- Name: TABLE daily_randomization; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.daily_randomization TO transcript_user;


--
-- Name: TABLE devices; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.devices TO transcript_user;


--
-- Name: SEQUENCE devices_device_id_seq; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT,USAGE ON SEQUENCE public.devices_device_id_seq TO transcript_user;


--
-- Name: TABLE experiment_config; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.experiment_config TO transcript_user;


--
-- Name: SEQUENCE experiment_config_config_id_seq; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON SEQUENCE public.experiment_config_config_id_seq TO transcript_user;


--
-- Name: TABLE queue_assignments; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.queue_assignments TO transcript_user;


--
-- Name: SEQUENCE queue_assignments_assignment_id_seq; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON SEQUENCE public.queue_assignments_assignment_id_seq TO transcript_user;


--
-- Name: TABLE queue_batches; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.queue_batches TO transcript_user;


--
-- Name: SEQUENCE queue_batches_batch_id_seq; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON SEQUENCE public.queue_batches_batch_id_seq TO transcript_user;


--
-- Name: TABLE ra_availability; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.ra_availability TO transcript_user;


--
-- Name: SEQUENCE ra_availability_availability_id_seq; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT,USAGE ON SEQUENCE public.ra_availability_availability_id_seq TO transcript_user;


--
-- Name: TABLE ra_devices; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.ra_devices TO transcript_user;


--
-- Name: TABLE second_review_queue; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.second_review_queue TO transcript_user;


--
-- Name: SEQUENCE second_review_queue_review_id_seq; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT,USAGE ON SEQUENCE public.second_review_queue_review_id_seq TO transcript_user;


--
-- Name: TABLE transcripts; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.transcripts TO transcript_user;


--
-- Name: TABLE user_classifications; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.user_classifications TO transcript_user;


--
-- Name: SEQUENCE user_classifications_classification_id_seq; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON SEQUENCE public.user_classifications_classification_id_seq TO transcript_user;


--
-- PostgreSQL database dump complete
--

\unrestrict ng9o0VxyaUsgEwhKEvV2UGBcrNCtAZ6lpg8n74j2ThpMXRRbUEfB3bwPLD9crPs

