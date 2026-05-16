export interface Project {
  id: string;
  title: string;
  description: string;
  items: string[]; // case_ids
  createdAt: string;
}

const STORAGE_KEY = 'legalsearch_projects';

export function getProjects(): Project[] {
  if (typeof window === 'undefined') return [];
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
  } catch {
    return [];
  }
}

export function saveProject(project: Project): void {
  const projects = getProjects();
  const idx = projects.findIndex((p) => p.id === project.id);
  if (idx >= 0) projects[idx] = project;
  else projects.push(project);
  localStorage.setItem(STORAGE_KEY, JSON.stringify(projects));
}

export function deleteProject(id: string): void {
  const projects = getProjects().filter((p) => p.id !== id);
  localStorage.setItem(STORAGE_KEY, JSON.stringify(projects));
}

export function addItemToProject(projectId: string, caseId: string): void {
  const projects = getProjects();
  const project = projects.find((p) => p.id === projectId);
  if (project && !project.items.includes(caseId)) {
    project.items.push(caseId);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(projects));
  }
}
