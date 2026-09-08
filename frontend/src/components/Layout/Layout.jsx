import { Outlet, useLocation } from "react-router-dom";
import { motion, AnimatePresence } from "framer-motion";
import Navbar from "./Navbar";
import Sidebar from "./Sidebar";
import AppFooter from "./AppFooter";
import AnimatedBackground from "./AnimatedBackground";

const PAGE_VARIANTS = {
  initial:  { opacity: 0, y: 8 },
  animate:  { opacity: 1, y: 0, transition: { duration: 0.18, ease: [0.16, 1, 0.3, 1] } },
  exit:     { opacity: 0, y: -4, transition: { duration: 0.12 } },
};

export default function Layout() {
  const location = useLocation();

  return (
    <div className="cp-layout">
      <AnimatedBackground />
      <Sidebar />
      <div className="cp-main">
        <Navbar />
        <main className="cp-content">
          <AnimatePresence mode="wait" initial={false}>
            <motion.div
              key={location.pathname}
              variants={PAGE_VARIANTS}
              initial="initial"
              animate="animate"
              exit="exit"
              style={{ height: "100%" }}
            >
              <Outlet />
            </motion.div>
          </AnimatePresence>
        </main>
        <AppFooter />
      </div>
    </div>
  );
}
