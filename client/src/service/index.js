import { $storex } from "../store"
import { ChatService } from "./chat"
import { ProjectService } from "./project"
import { WikiService } from "./wiki"
import { PrAnalysisService } from "./prAnalysis"
import { ContentService } from "./content"

const services = {
  $storex,
  chat: new ChatService($storex),
  project: new ProjectService($storex),
  wiki: new WikiService($storex),
  prAnalysis: new PrAnalysisService($storex),
  content: new ContentService($storex),
}
window.$services = services
export default services