export interface BackupEnvironmentVariable {
  name: string;
  value: string;
  source?: string;
}

export interface BackupParameter {
  name: string;
  type: string;
  default_value?: string;
  description?: string;
}

export interface BackupCredential {
  id: string;
  type: string;
  bindings: Record<string, string>;
  values: Record<string, string>;
  value_unavailable?: boolean;
}

export interface BackupScript {
  filename: string;
  type: string;
  phase: string;
  content: string;
}

export interface MavenConfig {
  root_pom?: string;
  goals?: string;
}

export interface BackupJobDetail {
  name: string;
  project_type: string;
  status: string;
  git_urls: string[];
  branches: string[];
  triggers: Array<Record<string, unknown>>;
  parameters: BackupParameter[];
  environment: {
    variables: BackupEnvironmentVariable[];
    tools: Record<string, unknown>;
  };
  credentials: BackupCredential[];
  scripts: BackupScript[];
  maven_config?: MavenConfig;
}

export interface BackupView {
  name: string;
  job_names: string[];
}

export interface BackupDetails {
  available: boolean;
  version: number;
  views: BackupView[];
  jobs: BackupJobDetail[];
}

function isRecord(value: unknown): value is Record<string, any> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function toStringRecord(value: unknown): Record<string, string> {
  if (!isRecord(value)) return {};
  return Object.fromEntries(
    Object.entries(value).filter(([, item]) => typeof item === 'string'),
  ) as Record<string, string>;
}

export function normalizeBackupDetails(input: unknown): BackupDetails {
  const source = isRecord(input) ? input : {};
  const jobs = Array.isArray(source.jobs)
    ? source.jobs.filter(isRecord).map((job) => {
        const environment = isRecord(job.environment) ? job.environment : {};
        const mavenConfig = isRecord(job.maven_config) ? job.maven_config : undefined;

        return {
          ...job,
          name: typeof job.name === 'string' ? job.name : '',
          project_type: job.project_type || '',
          status: job.status || 'enabled',
          git_urls: Array.isArray(job.git_urls) ? job.git_urls : [],
          branches: Array.isArray(job.branches) ? job.branches : [],
          triggers: Array.isArray(job.triggers) ? job.triggers.filter(isRecord) : [],
          parameters: Array.isArray(job.parameters)
            ? job.parameters.filter(isRecord).map((parameter) => ({
                ...parameter,
                name: typeof parameter.name === 'string' ? parameter.name : '',
                type: typeof parameter.type === 'string' ? parameter.type : '',
                default_value: typeof parameter.default_value === 'string'
                  ? parameter.default_value
                  : undefined,
                description: typeof parameter.description === 'string'
                  ? parameter.description
                  : undefined,
              }))
            : [],
          environment: {
            variables: Array.isArray(environment.variables)
              ? environment.variables.filter(isRecord).map((variable) => ({
                  ...variable,
                  name: typeof variable.name === 'string' ? variable.name : '',
                  value: typeof variable.value === 'string' ? variable.value : '',
                  source: typeof variable.source === 'string' ? variable.source : undefined,
                }))
              : [],
            tools: isRecord(environment.tools) ? environment.tools : {},
          },
          credentials: Array.isArray(job.credentials)
            ? job.credentials.filter(isRecord).map((credential) => ({
                ...credential,
                id: typeof credential.id === 'string' ? credential.id : '',
                type: typeof credential.type === 'string' ? credential.type : '',
                bindings: toStringRecord(credential.bindings),
                values: toStringRecord(credential.values),
                value_unavailable: typeof credential.value_unavailable === 'boolean'
                  ? credential.value_unavailable
                  : false,
              }))
            : [],
          scripts: Array.isArray(job.scripts)
            ? job.scripts.filter(isRecord).map((script) => ({
                ...script,
                filename: typeof script.filename === 'string' ? script.filename : '',
                type: typeof script.type === 'string' ? script.type : '',
                phase: typeof script.phase === 'string' ? script.phase : '',
                content: typeof script.content === 'string' ? script.content : '',
              }))
            : [],
          maven_config: mavenConfig ? {
            root_pom: mavenConfig.root_pom || '',
            goals: mavenConfig.goals || '',
          } : undefined,
        };
      })
    : [];
  const jobNames = new Set(
    jobs.map((job) => job.name).filter((name: unknown) => typeof name === 'string'),
  );
  let views: BackupView[] = Array.isArray(source.views)
    ? source.views
        .filter(isRecord)
        .filter((view) => typeof view.name === 'string' && view.name.length > 0)
        .map((view) => ({
          name: view.name,
          job_names: Array.from(new Set(
            (Array.isArray(view.job_names) ? view.job_names : [])
              .filter((name: unknown) => typeof name === 'string' && jobNames.has(name)),
          )),
        }))
    : [];

  if (jobNames.size === 0) {
    views = [];
  } else if (views.length === 0) {
    views = [{ name: '全部任务', job_names: Array.from(jobNames) }];
  } else {
    const referenced = new Set(views.flatMap((view) => view.job_names));
    const uncategorized = Array.from(jobNames).filter((name) => !referenced.has(name));
    if (uncategorized.length > 0) {
      views.push({ name: '未分类', job_names: uncategorized });
    }
  }

  return {
    available: source.available !== undefined ? Boolean(source.available) : Boolean(Array.isArray(source.jobs)),
    version: Number(source.version || 0),
    views,
    jobs,
  };
}

export function environmentCopyText(variable: BackupEnvironmentVariable): string {
  return `${variable.name}=${variable.value}`;
}

export function credentialCopyText(credential: BackupCredential): string {
  return Object.entries(credential.bindings)
    .map(([valueKey, variableName]) => `${variableName}=${credential.values[valueKey] || ''}`)
    .join('\n');
}


export function scriptDisplayName(
  script: Pick<BackupScript, 'type' | 'filename'>,
  index: number,
): string {
  const typeNames: Record<string, string> = {
    pipeline: '流水线脚本',
    maven: 'Maven 构建',
    gradle: 'Gradle 构建',
    batch: 'Windows 批处理',
  };
  if (typeNames[script.type]) return typeNames[script.type];

  const chineseNumbers = ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十'];
  return '构建脚本' + (chineseNumbers[index] || index + 1);
}