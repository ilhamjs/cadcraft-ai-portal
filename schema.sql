-- ============================================================================
-- CADCraft AI Agent Web Portal - Supabase Schema & Storage Setup
-- Copy and paste this entire script into the Supabase SQL Editor and click RUN
-- ============================================================================

-- Enable UUID generation extension
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ----------------------------------------------------------------------------
-- 1. Table: worker_status (Singleton row id = 1 for Ilham's laptop heartbeat)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.worker_status (
    id int8 PRIMARY KEY DEFAULT 1,
    is_online boolean NOT NULL DEFAULT false,
    last_heartbeat timestamptz NOT NULL DEFAULT (now() - interval '10 minutes'),
    current_job_id uuid NULL,
    CONSTRAINT worker_status_singleton CHECK (id = 1)
);

-- Insert initial singleton row if it doesn't exist
INSERT INTO public.worker_status (id, is_online, last_heartbeat, current_job_id)
VALUES (1, false, now() - interval '10 minutes', NULL)
ON CONFLICT (id) DO NOTHING;

-- ----------------------------------------------------------------------------
-- 2. Table: cad_jobs (Stores CAD modification requests & file paths)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.cad_jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    requester_name text NOT NULL,
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('waiting_offline', 'queued', 'approved', 'processing', 'completed', 'failed')),
    input_dwg_url text NOT NULL,
    reference_img_urls jsonb NOT NULL DEFAULT '[]'::jsonb,
    user_prompt text NOT NULL,
    output_dwg_url text NULL,
    output_pdf_url text NULL,
    agent_response text NULL
);

CREATE INDEX IF NOT EXISTS idx_cad_jobs_status ON public.cad_jobs (status);
CREATE INDEX IF NOT EXISTS idx_cad_jobs_created_at ON public.cad_jobs (created_at DESC);

-- ----------------------------------------------------------------------------
-- 3. Table: chat_messages (Stores conversation history per CAD job)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.chat_messages (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id uuid NOT NULL REFERENCES public.cad_jobs(id) ON DELETE CASCADE,
    sender text NOT NULL CHECK (sender IN ('user', 'assistant')),
    message text NOT NULL,
    attachments jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_job_id ON public.chat_messages (job_id, created_at ASC);

-- ----------------------------------------------------------------------------
-- 4. Row Level Security (RLS) Policies (Open for Portal Anon/Service Key Usage)
-- ----------------------------------------------------------------------------
ALTER TABLE public.worker_status ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.cad_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.chat_messages ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Allow full access to worker_status" ON public.worker_status;
CREATE POLICY "Allow full access to worker_status"
    ON public.worker_status FOR ALL
    USING (true)
    WITH CHECK (true);

DROP POLICY IF EXISTS "Allow full access to cad_jobs" ON public.cad_jobs;
CREATE POLICY "Allow full access to cad_jobs"
    ON public.cad_jobs FOR ALL
    USING (true)
    WITH CHECK (true);

DROP POLICY IF EXISTS "Allow full access to chat_messages" ON public.chat_messages;
CREATE POLICY "Allow full access to chat_messages"
    ON public.chat_messages FOR ALL
    USING (true)
    WITH CHECK (true);

-- ----------------------------------------------------------------------------
-- 5. Storage Bucket: cad-files (Stores .dwg, .png/.jpg, and output .pdf)
-- ----------------------------------------------------------------------------
INSERT INTO storage.buckets (id, name, public)
VALUES ('cad-files', 'cad-files', true)
ON CONFLICT (id) DO UPDATE SET public = true;

DROP POLICY IF EXISTS "Public Access to cad-files" ON storage.objects;
CREATE POLICY "Public Access to cad-files"
    ON storage.objects FOR ALL
    USING (bucket_id = 'cad-files')
    WITH CHECK (bucket_id = 'cad-files');
