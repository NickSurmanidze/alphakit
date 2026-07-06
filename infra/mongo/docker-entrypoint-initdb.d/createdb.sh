#!/usr/bin/env bash
echo "Creating mongo database..."
# https://docs.mongodb.com/manual/tutorial/write-scripts-for-the-mongo-shell/
mongosh admin --host localhost -u mongo -p mongo --eval "db = db.getSiblingDB('mongo'); db.createUser({ user: 'mongo', pwd: 'mongo', roles: [{ role: 'dbOwner', db: 'mongo' }] });"
echo "Mongo database created."
