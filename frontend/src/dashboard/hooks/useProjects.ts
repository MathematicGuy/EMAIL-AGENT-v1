import { useCallback, useEffect, useState } from 'react';
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
  const [activeProjectId, setActiveProjectIdState] = useState(
    () => window.localStorage.getItem(ACTIVE_PROJECT_KEY) ?? ''
  );
  const [error, setError] = useState<string | null>(null);

  const refreshProjects = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/v1/cowork/chat/projects`, {
        credentials: 'include',
      });
      if (response.status === 401) {
        setProjects([]);
        setError(null);
        return;
      }
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = (await response.json()) as { projects: BackendProject[] };
      const next = payload.projects.map(fromBackend);
      setProjects(next);
      setActiveProjectIdState((current) => {
        const selected = next.some((project) => project.id === current)
          ? current
          : next.find((project) => project.isDefault)?.id ?? next[0]?.id ?? '';
        if (selected) window.localStorage.setItem(ACTIVE_PROJECT_KEY, selected);
        return selected;
      });
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Project API unavailable');
    }
  }, []);

  useEffect(() => {
    queueMicrotask(() => void refreshProjects());
  }, [refreshProjects]);

  const setActiveProjectId = useCallback((projectId: string) => {
    setActiveProjectIdState(projectId);
    window.localStorage.setItem(ACTIVE_PROJECT_KEY, projectId);
  }, []);

  const createProject = useCallback(async (input: Pick<Project, 'name' | 'icon' | 'color'>) => {
    const response = await fetch(`${API_BASE_URL}/v1/cowork/chat/projects`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: input.name.trim() }),
    });
    if (!response.ok) throw new Error(`Could not create project (HTTP ${response.status})`);
    const project = fromBackend((await response.json()) as BackendProject);
    setProjects((current) => [...current, project]);
    setActiveProjectId(project.id);
    return project;
  }, [setActiveProjectId]);

  return {
    projects,
    activeProjectId,
    setActiveProjectId,
    createProject,
    refreshProjects,
    error,
  };
}
