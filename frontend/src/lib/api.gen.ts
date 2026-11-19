// api.gen.ts — GENERATED from src/lib/openapi.json by scripts/gen-types.mjs; do not edit. Exports: paths, components.
export interface paths {
    "/api/events": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Events */
        get: operations["events_api_events_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/jobs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Jobs */
        get: operations["list_jobs_api_jobs_get"];
        put?: never;
        /** Create Jobs */
        post: operations["create_jobs_api_jobs_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/jobs/{job_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** Cancel Job */
        delete: operations["cancel_job_api_jobs__job_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/jobs/{job_id}/logs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Job Logs
         * @description A job's log lines with the backend's timestamps, oldest first, so a fresh tab shows its history.
         */
        get: operations["job_logs_api_jobs__job_id__logs_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/library": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Library */
        get: operations["get_library_api_library_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/settings": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Settings */
        get: operations["get_settings_api_settings_get"];
        /** Put Settings */
        put: operations["put_settings_api_settings_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/subtitles/{video_id}/{lang}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Subtitles */
        get: operations["get_subtitles_api_subtitles__video_id___lang__get"];
        /** Put Subtitles */
        put: operations["put_subtitles_api_subtitles__video_id___lang__put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/system": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** System */
        get: operations["system_api_system_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /** Cue */
        Cue: {
            /** End Ms */
            end_ms: number;
            /**
             * Flags
             * @description Set by the backend on GET; ignored on PUT.
             */
            flags?: ("too_fast" | "long_line" | "broken_text" | "duplicate" | "untranslated")[];
            /**
             * Id
             * @description Position in the source cue list; the translator keys on it.
             */
            id: number;
            /**
             * Source Text
             * @description The cue it was translated from, when translated.
             */
            source_text?: string | null;
            /** Start Ms */
            start_ms: number;
            /** Text */
            text: string;
        };
        /**
         * CueLimits
         * @description The gate's per-line limits (core/gate THRESHOLDS), sent so the screen can say « over 35 characters a second ».
         */
        CueLimits: {
            /** Max Cps */
            max_cps: number;
            /** Max Line Chars */
            max_line_chars: number;
        };
        /** CueList */
        CueList: {
            /** Cues */
            cues: components["schemas"]["Cue"][];
            /**
             * From Language
             * @description Language of source_text, when translated.
             */
            from_language?: string | null;
            /** Language */
            language: string;
            /** @description Set by the backend on GET; ignored on PUT. */
            limits?: components["schemas"]["CueLimits"] | null;
            /**
             * Origin
             * @enum {string}
             */
            origin: "kept" | "downloaded" | "translated" | "transcribed" | "existing";
            /**
             * Reviewed
             * @default false
             */
            reviewed?: boolean;
            /** Video Id */
            video_id: string;
        };
        /**
         * EventFrameDoc
         * @description Documents the SSE payload for OpenAPI: each `data:` line is one of these, discriminated by `kind`.
         */
        EventFrameDoc: {
            /** Frame */
            frame: components["schemas"]["JobEvent"] | components["schemas"]["LogEvent"] | components["schemas"]["LibraryChangedEvent"];
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** Job */
        Job: {
            /**
             * Batch Id
             * @description Same for every job one POST /api/jobs created: the Queue's overall bar counts them together.
             */
            batch_id?: string | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Error
             * @description One sentence a person understands, when state is 'failed'.
             */
            error?: string | null;
            /** Finished At */
            finished_at?: string | null;
            /** Id */
            id: string;
            /** Languages */
            languages: string[];
            /**
             * Position
             * @description Place in the queue (0 = next); null once started.
             */
            position?: number | null;
            /** Stages */
            stages: components["schemas"]["StageProgress"][];
            /** Started At */
            started_at?: string | null;
            /**
             * State
             * @enum {string}
             */
            state: "queued" | "running" | "done" | "failed" | "cancelled";
            /** Video Id */
            video_id: string;
            /** Video Name */
            video_name: string;
        };
        /** JobCreate */
        JobCreate: {
            /**
             * Languages
             * @description Null = every wanted language that is missing for each video ('Fill missing').
             */
            languages?: string[] | null;
            /** @default {
             *       "force_asr": false,
             *       "sync_offset_ms": 0
             *     } */
            options?: components["schemas"]["JobOptions"];
            /** Video Ids */
            video_ids: string[];
        };
        /** JobCreateResponse */
        JobCreateResponse: {
            /** Jobs */
            jobs: components["schemas"]["Job"][];
            /**
             * Skipped
             * @description Video ids with nothing missing in the requested languages.
             */
            skipped: string[];
        };
        /** JobEvent */
        JobEvent: {
            job: components["schemas"]["Job"];
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            kind: "job";
        };
        /** JobList */
        JobList: {
            /**
             * Jobs
             * @description Running first, then queued by position, then history newest first.
             */
            jobs: components["schemas"]["Job"][];
        };
        /**
         * JobOptions
         * @description The 'Advanced' overrides of one video; every field defaults to 'let the ladder decide'.
         */
        JobOptions: {
            /**
             * Force Asr
             * @description Ignore every existing subtitle and transcribe.
             * @default false
             */
            force_asr?: boolean;
            /**
             * Source Track
             * @description Path or 'stream:N' of the subtitle to translate from.
             */
            source_track?: string | null;
            /**
             * Sync Offset Ms
             * @description Milliseconds added to every cue; positive = later.
             * @default 0
             */
            sync_offset_ms?: number;
        };
        /** LanguageCoverage */
        LanguageCoverage: {
            /**
             * From Language
             * @description For translated/transcribed+translated: the source language.
             */
            from_language?: string | null;
            /**
             * Job Id
             * @description Set when state is queued/running/failed.
             */
            job_id?: string | null;
            /**
             * Language
             * @description The wanted language as in Settings, 'ov' included.
             */
            language: string;
            /**
             * Origin
             * @description Set when state is 'passed'; null = a SubStudio track of unrecorded origin.
             */
            origin?: ("kept" | "downloaded" | "translated" | "transcribed" | "existing") | null;
            /**
             * Path
             * @description Sidecar path when the subtitle is a file.
             */
            path?: string | null;
            /**
             * Reason
             * @description One sentence: why it failed, why it is unknown, or what was ignored.
             */
            reason?: string | null;
            /**
             * Resolved
             * @description For 'ov': the audio's language when known ('en'); else equals language.
             */
            resolved?: string | null;
            /**
             * Rung
             * @description Set when known (from the job's cell verdict).
             */
            rung?: ("existing" | "download" | "translate" | "asr") | null;
            /**
             * State
             * @enum {string}
             */
            state: "passed" | "missing" | "unchecked" | "pending" | "running" | "failed" | "unknown";
        };
        /**
         * LibraryChangedEvent
         * @description Coverage of these videos changed (a job wrote a subtitle): refetch the library.
         */
        LibraryChangedEvent: {
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            kind: "library";
            /** Video Ids */
            video_ids: string[];
        };
        /** LibraryResponse */
        LibraryResponse: {
            /**
             * Languages
             * @description The wanted languages the coverage was computed for.
             */
            languages: string[];
            /** Root */
            root: string;
            /**
             * Scanned At
             * Format: date-time
             */
            scanned_at: string;
            /** Videos */
            videos: components["schemas"]["LibraryVideo"][];
            /**
             * Warnings
             * @description Folders or files that could not be read; shown, never dropped silently.
             */
            warnings: string[];
        };
        /** LibraryVideo */
        LibraryVideo: {
            /**
             * Audio Language
             * @description Language tag of the first audio track (OV), null when untagged.
             */
            audio_language?: string | null;
            /**
             * Coverage
             * @description One entry per wanted language, in Settings order.
             */
            coverage: components["schemas"]["LanguageCoverage"][];
            /**
             * Duration S
             * @description Seconds; null when ffprobe could not read it.
             */
            duration_s?: number | null;
            /**
             * Folder
             * @description Folder relative to the media root, '' for the root itself; '/' separated.
             */
            folder: string;
            /**
             * Id
             * @description Stable id derived from the path; use it in every URL instead of the path.
             */
            id: string;
            /**
             * Marker Languages
             * @description Languages the file name claims, '(Sub-EN,FR)' -> ['en','fr']; a claim, not a proof.
             */
            marker_languages?: string[] | null;
            /**
             * Mtime
             * Format: date-time
             */
            mtime: string;
            /** Name */
            name: string;
            /** Path */
            path: string;
            /**
             * Probe Error
             * @description Set when ffprobe failed: coverage is then unknown, not missing.
             */
            probe_error?: string | null;
            /** Size Bytes */
            size_bytes: number;
            /**
             * Tracks
             * @description Every subtitle found, counted or not.
             */
            tracks: components["schemas"]["SubtitleTrack"][];
            /**
             * Validated
             * @description Every wanted language passed as a SubStudio track: Start skips this video.
             */
            validated: boolean;
        };
        /** LogEvent */
        LogEvent: {
            /**
             * Job Id
             * @description Null for system-wide lines.
             */
            job_id?: string | null;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            kind: "log";
            /**
             * Level
             * @enum {string}
             */
            level: "DEBUG" | "INFO" | "WARNING" | "ERROR";
            /** Message */
            message: string;
            /**
             * Ts
             * Format: date-time
             * @description When the backend emitted the line, not when the browser received it.
             */
            ts: string;
        };
        /** ProviderStatus */
        ProviderStatus: {
            /**
             * Configured
             * @description A key is set; the value itself is never sent.
             */
            configured: boolean;
            /** Name */
            name: string;
        };
        /**
         * Settings
         * @description D-0002: languages are the only choice on the main path; the output is always the source file,
         *     replaced in place after the check, renamed with '(Sub-OV,EN,FR)'.
         */
        Settings: {
            /**
             * Count Existing
             * @description Count subtitles not made by this SubStudio (core/provenance.py) when the quality gate passes them (on by default: Cyril, 2026-11-19, D-0002). Off: listed, never counted, never checked.
             * @default true
             */
            count_existing?: boolean;
            /**
             * Languages
             * @description Wanted languages, 'ov' included: the only list every picker reads.
             */
            languages: string[];
        };
        /** SettingsResponse */
        SettingsResponse: {
            /** Providers */
            providers: components["schemas"]["ProviderStatus"][];
            settings: components["schemas"]["Settings"];
        };
        /** StageProgress */
        StageProgress: {
            /**
             * Detail
             * @description e.g. which rung was used: 'downloaded from OpenSubtitles'.
             */
            detail?: string | null;
            /**
             * Eta S
             * @description Seconds left in this stage; null when unknown.
             */
            eta_s?: number | null;
            /**
             * Progress
             * @description 0..1 within this stage only; never across stages.
             * @default 0
             */
            progress?: number;
            /**
             * Stage
             * @enum {string}
             */
            stage: "check" | "download" | "transcribe" | "correct" | "translate" | "write";
            /**
             * State
             * @enum {string}
             */
            state: "pending" | "running" | "done" | "skipped" | "failed";
        };
        /**
         * SubtitleTrack
         * @description One subtitle found for a video, counted as coverage or not (and why).
         */
        SubtitleTrack: {
            /** Codec */
            codec?: string | null;
            /**
             * Counted
             * @description True when this track counts as coverage for its language.
             */
            counted: boolean;
            /**
             * Kind
             * @enum {string}
             */
            kind: "embedded" | "sidecar";
            /**
             * Language
             * @description Normalised code; null when the tag is absent or 'und'.
             */
            language?: string | null;
            /**
             * Language Tag
             * @description The raw tag as found (ffprobe tag or filename part).
             */
            language_tag?: string | null;
            /**
             * Made By Substudio
             * @description Carries the SubStudio marker (core/provenance.py).
             * @default false
             */
            made_by_substudio?: boolean;
            /**
             * Origin
             * @description SubStudio tracks only: the SUBSTUDIO_ORIGIN stream tag, null when not recorded.
             */
            origin?: ("kept" | "downloaded" | "translated" | "transcribed") | null;
            /**
             * Path
             * @description Absolute path, sidecar files only.
             */
            path?: string | null;
            /**
             * Reason
             * @description One sentence when counted is false, e.g. 'not made by this SubStudio (Settings)'.
             */
            reason?: string | null;
            /**
             * Stream Index
             * @description ffprobe stream index, embedded tracks only.
             */
            stream_index?: number | null;
            /** Title */
            title?: string | null;
        };
        /** SystemStatus */
        SystemStatus: {
            /**
             * Cpu Percent
             * @description Whole machine, 0..100.
             */
            cpu_percent?: number | null;
            /**
             * Cpu Threads Budget
             * @description D-0001: 4 of 8 threads.
             * @default 4
             */
            cpu_threads_budget?: number;
            /** Loaded Model */
            loaded_model?: string | null;
            /** Media Root */
            media_root: string;
            /**
             * Ram Budget Mb
             * @description D-0001: about half of this 15 GB machine.
             * @default 7168
             */
            ram_budget_mb?: number;
            /**
             * Ram Used Mb
             * @description This backend process, MB.
             */
            ram_used_mb?: number | null;
            /**
             * Stub
             * @description True when the API serves stub data (SUBSTUDIO_API_STUB=1): the UI must say so.
             */
            stub: boolean;
        };
        /** ValidationError */
        ValidationError: {
            /** Context */
            ctx?: Record<string, never>;
            /** Input */
            input?: unknown;
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    events_api_events_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description text/event-stream; each `data:` line is EventFrameDoc.frame (discriminated by `kind`). On connect: one job frame per unfinished job, then live frames. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EventFrameDoc"];
                };
            };
        };
    };
    list_jobs_api_jobs_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["JobList"];
                };
            };
        };
    };
    create_jobs_api_jobs_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["JobCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["JobCreateResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    cancel_job_api_jobs__job_id__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Job"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    job_logs_api_jobs__job_id__logs_get: {
        parameters: {
            query?: {
                limit?: number;
            };
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["LogEvent"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_library_api_library_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["LibraryResponse"];
                };
            };
        };
    };
    get_settings_api_settings_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SettingsResponse"];
                };
            };
        };
    };
    put_settings_api_settings_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Settings"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SettingsResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_subtitles_api_subtitles__video_id___lang__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                lang: string;
                video_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CueList"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    put_subtitles_api_subtitles__video_id___lang__put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                lang: string;
                video_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CueList"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CueList"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    system_api_system_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SystemStatus"];
                };
            };
        };
    };
}
