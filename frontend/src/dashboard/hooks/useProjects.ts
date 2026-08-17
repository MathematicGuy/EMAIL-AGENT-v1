import { useCallback, useEffect, useRef, useState } from 'react';
import { API_BASE_URL } from '../../lib/apiConfig';
import type { Project } from '../types/projectTypes';

const ACTIVE_PROJECT_KEY = 'v-assistant-active-project-id';

interface BackendProject {
  project_id: string;
  name: string;
  is_default: boolean;
  created_at: string;
}

function fromBackend(project: BackendProject): Project {
  return {
    id: project.project_id,
    name: project.name,
    isDefault: project.is_default,
    icon: '📁',
    color: project.is_default ? '#d97757' : '#8b7cf6',
    createdAt: project.created_at,
  };
}

export function useProjects() {
  const [projects, setProjects] = useState<Project[]>([]);
  const projectMutationVersion = useRef(0);
  const guestSessionPromise = useRef<Promise<void> | null>(null);
  const [activeProjectId, setActiveProjectIdState] = useState(
    () => window.localStorage.getItem(ACTIVE_PROJECT_KEY) ?? ''
  );
  const [error, setError] = useState<string | null>(null);

  const ensureGuestSession = useCallback(async (): Promise<void> => {
    if (!guestSessionPromise.current) {
      guestSessionPromise.current = fetch(`${API_BASE_URL}/v1/cowork/chat/guest-session`, {
        method: 'POST',
        credentials: 'include',
      }).then((response) => {
        if (!response.ok) throw new Error(`Could not start guest chat (HTTP ${response.status})`);
      });
    }
    try {
      await guestSessionPromise.current;
    } catch (cause) {
      guestSessionPromise.current = null;
      throw cause;
    }
  }, []);

  const refreshProjects = useCallback(async (): Promise<Project[]> => {
    const refreshVersion = projectMutationVersion.current;
    try {
      await ensureGuestSession();
      let response = await fetch(`${API_BASE_URL}/v1/cowork/chat/projects`, {
        credentials: 'include',
      });
      if (response.status === 401) {
        guestSessionPromise.current = null;
        await ensureGuestSession();
        response = await fetch(`${API_BASE_URL}/v1/cowork/chat/projects`, {
          credentials: 'include',
        });
      }
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = (await response.json()) as { projects: BackendProject[] };
      const next = payload.projects.map(fromBackend);
      if (refreshVersion !== projectMutationVersion.current) return next;
      setProjects(next);
      setActiveProjectIdState((current) => {
        const selected = next.some((project) => project.id === current)
          ? current
          : next.find((project) => project.isDefault)?.id ?? next[0]?.id ?? '';
        if (selected) window.localStorage.setItem(ACTIVE_PROJECT_KEY, selected);
        return selected;
      });
      setError(null);
      return next;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Project API unavailable');
      return [];
    }
  }, [ensureGuestSession]);

  useEffect(() => {
    queueMicrotask(() => void refreshProjects());
  }, [refreshProjects]);

  const setActiveProjectId = useCallback((projectId: string) => {
    setActiveProjectIdState(projectId);
    window.localStorage.setItem(ACTIVE_PROJECT_KEY, projectId);
  }, []);

  const createProject = useCallback(async (input: Pick<Project, 'name' | 'icon' | 'color'>) => {
    await ensureGuestSession();
    const response = await fetch(`${API_BASE_URL}/v1/cowork/chat/projects`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: input.name.trim() }),
    });
    if (!response.ok) throw new Error(`Could not create project (HTTP ${response.status})`);
    const project = fromBackend((await response.json()) as BackendProject);
    projectMutationVersion.current += 1;
    setProjects((current) => [...current, project]);
    setActiveProjectId(project.id);
    return project;
  }, [ensureGuestSession, setActiveProjectId]);

  const ensureDefaultProject = useCallback(async (): Promise<Project> => {
    const next = await refreshProjects();
    const project = next.find((item) => item.isDefault) ?? next[0];
    if (!project) throw new Error('Could not initialize a project for chat.');
    setActiveProjectId(project.id);
    return project;
  }, [refreshProjects, setActiveProjectId]);

  return {
    projects,
    activeProjectId,
    setActiveProjectId,
    createProject,
    ensureDefaultProject,
    refreshProjects,
    error,
  };
}
