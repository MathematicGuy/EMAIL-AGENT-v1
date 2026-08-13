export interface Project {
  id: string;
  name: string;
  isDefault?: boolean;
  icon?: string;
  color?: string;
  createdAt: string;
}

export const DEFAULT_PROJECT: Project = {
  id: 'default-project',
  name: 'Default Project',
  isDefault: true,
  icon: '📁',
  color: '#d97757',
  createdAt: '2020-01-01T00:00:00.000Z',
};
