import { useCallback, useEffect, useState } from 'react';
import { DEFAULT_PROJECT } from '../types/projectTypes';
import type { Project } from '../types/projectTypes';

const PROJECTS_KEY = 'v-assistant-projects';
const ACTIVE_PROJECT_KEY = 'v-assistant-active-project-id';

function readProjects(): Project[] {
  try {
    const value = window.localStorage.getItem(PROJECTS_KEY);
    if (!value) return [DEFAULT_PROJECT];
    const parsed: unknown = JSON.parse(value);
    if (!Array.isArray(parsed)) return [DEFAULT_PROJECT];
    const projects = parsed.filter(
      (project): project is Project =>
        typeof project === 'object' && project !== null &&
        typeof (project as Project).id === 'string' &&
        typeof (project as Project).name === 'string'
    );
    return projects.some((project) => project.id === DEFAULT_PROJECT.id)
      ? projects
      : [DEFAULT_PROJECT, ...projects];
  } catch {
    return [DEFAULT_PROJECT];
  }
}

export function useProjects() {
  const [projects, setProjects] = useState<Project[]>(readProjects);
  const [activeProjectId, setActiveProjectIdState] = useState(() => {
    const stored = window.localStorage.getItem(ACTIVE_PROJECT_KEY);
    return readProjects().some((project) => project.id === stored)
      ? stored!
      : DEFAULT_PROJECT.id;
  });

  useEffect(() => {
    window.localStorage.setItem(PROJECTS_KEY, JSON.stringify(projects));
  }, [projects]);

  useEffect(() => {
    window.localStorage.setItem(ACTIVE_PROJECT_KEY, activeProjectId);
  }, [activeProjectId]);

  const setActiveProjectId = useCallback((projectId: string) => {
    if (projects.some((project) => project.id === projectId)) {
      setActiveProjectIdState(projectId);
    }
  }, [projects]);

  const createProject = useCallback((input: Pick<Project, 'name' | 'icon' | 'color'>) => {
    const project: Project = {
      id: `project_${crypto.randomUUID?.() ?? `${Date.now()}_${Math.random().toString(16).slice(2)}`}`,
      name: input.name.trim(),
      icon: input.icon || '📁',
      color: input.color || '#d97757',
      createdAt: new Date().toISOString(),
    };
    setProjects((current) => [...current, project]);
    setActiveProjectIdState(project.id);
    return project;
  }, []);

  return { projects, activeProjectId, setActiveProjectId, createProject };
}
