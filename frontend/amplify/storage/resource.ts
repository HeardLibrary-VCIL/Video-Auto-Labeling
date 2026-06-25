import { defineStorage } from '@aws-amplify/backend';

// Storage paths — customize these to match your pipeline output structure
export const storage = defineStorage({
  name: 'video-autolabeling-storage',
  access: (allow) => ({
    'video/*': [
      allow.authenticated.to(['read', 'write']),
    ],
    'result/*': [
      allow.authenticated.to(['read', 'write']),
    ],
    'visual_result/*': [
      allow.authenticated.to(['read', 'write']),
    ],
    'edit/*': [
      allow.authenticated.to(['read', 'write']),
    ],
    'ai_result/*': [
      allow.authenticated.to(['read', 'write']),
    ],
    'evaluation/*': [
      allow.authenticated.to(['read', 'write']),
    ],
  }),
});
