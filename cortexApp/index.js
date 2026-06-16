const fs = require('fs');
const path = require('path');
const readline = require('readline');
const { stdin, stdout } = process;
const rl = readline.createInterface({ input: stdin, output: stdout });

let tasks = [];

rl.question('Enter a task (or /no_think /think"