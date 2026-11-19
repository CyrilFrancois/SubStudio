FROM node:18-alpine

WORKDIR /app

# 1. Copy package files first
COPY package*.json ./

# 2. Force install including devDependencies
RUN npm install

# 3. Copy configuration files specifically
COPY tailwind.config.ts ./
COPY postcss.config.js ./
COPY tsconfig.json ./

# 4. Copy the rest of the source code
COPY . .

EXPOSE 3000

# Inside a container the server must listen on every interface for the port mapping to reach it;
# on the host, package.json binds 127.0.0.1 so the app is never exposed to the network.
CMD ["npx", "next", "dev", "-H", "0.0.0.0"]