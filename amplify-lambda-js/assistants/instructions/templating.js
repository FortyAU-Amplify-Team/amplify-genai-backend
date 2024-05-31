import Handlebars from "handlebars";
import yaml from "js-yaml";
import {formatOps, getOps} from "../ops/ops.js";

const extractTagAndFormat = (str) => {
    const regex = /\{\{\s*ops\s+([a-zA-Z0-9_./-]+)?(:[a-zA-Z0-9_./-]+)?\s*\}\}/;
    const match = str.match(regex);

    if (match) {
        return { tag: match[1], format: match[2] };
    } else {
        return { tag: null, format: null };
    }
}


export const fillInTemplate = async (llm, params, body, ds, templateStr, contextData) => {

    contextData = {
        ...contextData,
        user: params.account.user,
    }

    let result = templateStr;
    try {

        let opsStr = "";
        const { tag, format } = extractTagAndFormat(templateStr);
        if(tag || templateStr.includes("__assistantOps")) {
            const ops = await getOps(params.account.accessToken, tag);

            if(tag) {
                opsStr = await formatOps(ops, format);
            }
            contextData["__assistantOps"] = ops;

            llm.sendStateEventToStream({resolvedOps: ops})
        }

        Handlebars.registerHelper('ops', function (tagandformat) {
            return opsStr;
        });

        Handlebars.registerHelper('assistantName', function () {
            return contextData.assistant.name;
        });

        Handlebars.registerHelper('user', function () {
            return contextData.user;
        });

        Handlebars.registerHelper('datetime', function (fmt) {
            // Output a date string in the provided fmt
            return new Date().toISOString();
        });

        Handlebars.registerHelper('yaml', function (context) {
            return yaml.dump(context);
        });

        const template = Handlebars.compile(templateStr);
        result = template(contextData);

    } catch (e) {
        console.error(e);
    }

    return result;
}