import { hashPassword } from '../src/modules/auth/password.js';
import { closeMongoClient } from '../src/db/mongo.js';
import { createUser, getUserByEmail } from '../src/modules/users/users.repository.js';

const parseArgs = () => {
  const args = process.argv.slice(2);
  const get = (flag: string) => {
    const index = args.indexOf(flag);
    return index !== -1 ? args[index + 1] : undefined;
  };

  const email = get('--email');
  const password = get('--password');

  if (!email || !password) {
    console.error('Usage: pnpm seed:user -- --email <email> --password <password>');
    process.exit(1);
  }

  return { email, password };
};

const main = async () => {
  const { email, password } = parseArgs();

  const existing = await getUserByEmail(email);
  if (existing) {
    console.error(`User ${email} already exists. Delete it first if you want to recreate it.`);
    process.exit(1);
  }

  const passwordHash = await hashPassword(password);
  const user = await createUser({ email, passwordHash });

  console.log(`Created user ${user.email} (${user._id.toHexString()})`);
};

main()
  .catch(err => {
    console.error(err);
    process.exitCode = 1;
  })
  .finally(() => closeMongoClient());
