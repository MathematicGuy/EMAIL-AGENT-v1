export interface Project {
  id: string;
  name: string;
  icon?: string;
  color?: string;
  createdAt: string;
}

export const DEFAULT_PROJECT: Project = {
  id: 'demo-project',
  name: 'Demo Project',
  icon: '📁',
  color: '#d97757',
  createdAt: '2020-01-01T00:00:00.000Z',
};
