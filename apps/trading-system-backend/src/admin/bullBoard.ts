import { createBullBoard } from '@bull-board/api';
import { BullMQAdapter } from '@bull-board/api/bullMQAdapter';
import { ExpressAdapter } from '@bull-board/express';

import { queues } from '../queue/queues.js';

export const BULL_BOARD_BASE_PATH = '/admin/queues';

export const createBullBoardRouter = () => {
  const serverAdapter = new ExpressAdapter();
  serverAdapter.setBasePath(BULL_BOARD_BASE_PATH);

  createBullBoard({
    queues: Object.values(queues).map(queue => new BullMQAdapter(queue)),
    serverAdapter
  });

  return serverAdapter.getRouter();
};
