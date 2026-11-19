// types.ts — Named aliases over the generated contract (api.gen.ts); never hand-written shapes. Exports: every type below.
import type { components } from './api.gen';

type S = components['schemas'];

export type LibraryResponse = S['LibraryResponse'];
export type LibraryVideo = S['LibraryVideo'];
export type LanguageCoverage = S['LanguageCoverage'];
export type CoverageState = LanguageCoverage['state'];
export type CoverageOrigin = NonNullable<LanguageCoverage['origin']>;
export type SubtitleTrack = S['SubtitleTrack'];

export type Job = S['Job'];
export type JobState = Job['state'];
export type JobCreate = S['JobCreate'];
export type JobOptions = S['JobOptions'];
export type JobCreateResponse = S['JobCreateResponse'];
export type JobList = S['JobList'];
export type StageProgress = S['StageProgress'];

export type EventFrame = S['EventFrameDoc']['frame'];
export type LogEvent = S['LogEvent'];

export type Settings = S['Settings'];
export type SettingsResponse = S['SettingsResponse'];
export type ProviderStatus = S['ProviderStatus'];
export type SystemStatus = S['SystemStatus'];
export type CueList = S['CueList'];
